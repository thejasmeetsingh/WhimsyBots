# Architecture & Data Models v1.0

## 1. What We're Building

A self-hosted, Django-based AI agent platform where users can create and configure AI-powered "apps" (e.g. a Journaling Agent, a Research Assistant, a Daily Briefing bot) through an admin panel. Each app runs on a schedule, communicates with users via SMS (AWS Pinpoint) and Email (AWS SES), processes replies using a local LLM (Ollama), and can generate rich PDF reports on demand.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Django (Core)                        │
│  Admin Panel  │  REST API  │  Models  │  Business Logic  │
└───────────────────────────┬─────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
   ┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────┐
   │   Celery     │  │   Ollama     │  │   AWS        │
   │   Workers    │  │  (Local LLM) │  │  SNS/SES/    │
   │  + Beat DB   │  │             │  │  Pinpoint    │
   └──────┬──────┘  └─────────────┘  └──────┬──────┘
          │                                  │
   ┌──────▼──────┐                   ┌───────▼──────┐
   │    Redis     │                  │  SQS Queue   │
   │   (Broker)   │                  │ (Inbound SMS)│
   └─────────────┘                   └─────────────┘
```

### Request Flows

#### Outbound (Scheduled App Run)
```
Celery Beat (every minute)
  → Master poller task checks all App objects
  → App.next_run <= now? → Trigger app_runner task(app_id)
  → app_runner builds context from ConversationHistory
  → Calls Ollama → gets response
  → Sends SMS via AWS SNS (or Email via SES)
  → Stores Message to DB
  → Updates App.next_run
```

#### Inbound (User SMS Reply)
```
User replies to SMS
  → AWS Pinpoint receives it
  → Pinpoint → SNS Topic → SQS Queue
  → Celery consumer polls SQS
  → Matches phone number to App + User
  → Checks intent: is this a report request?
    → YES: LLM generates HTML → WeasyPrint → PDF → SES sends email
    → NO: Normal reply processing → Ollama → SMS response
  → Stores Message to DB
```

#### Report Generation
```
Inbound message detected as report/summary request
  → LLM confirms intent
  → LLM generates full HTML report with inline CSS
  → WeasyPrint converts HTML → PDF (in memory)
  → AWS SES sends PDF as email attachment
  → SMS confirmation sent: "Your report has been sent to your email"
```

---

## 3. Django App Structure

```
/platform
  /core                  ← shared utilities, base classes, AWS clients
  /apps                  ← App model, configuration, admin
  /agents                ← Agent logic, Ollama client, intent detection
  /messaging             ← SMS + Email send/receive abstraction
  /conversations         ← Message history models
  /reports               ← PDF generation, HTML templates
  /scheduler             ← Celery tasks, master poller, app runner
  /users                 ← Extended user model, permissions
  manage.py
  celery.py
  settings.py
```

---

## 4. Data Models

### 4.1 `users` app

#### `User` (extends AbstractUser)
```python
class User(AbstractUser):
    phone_number    = models.CharField(max_length=20, unique=True, null=True)
    email           = models.EmailField(unique=True)
    timezone        = models.CharField(max_length=50, default='UTC')
    is_platform_admin = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

### 4.2 `apps` app

#### `App`
The central model. Every agent/bot the user creates is an App instance.

```python
class App(models.Model):

    class Status(models.TextChoices):
        ACTIVE   = 'active'
        PAUSED   = 'paused'
        DRAFT    = 'draft'

    class TriggerType(models.TextChoices):
        SCHEDULED = 'scheduled'   # runs on interval
        INBOUND   = 'inbound'     # only runs when user messages first
        BOTH      = 'both'

    owner           = models.ForeignKey(User, on_delete=models.CASCADE)
    name            = models.CharField(max_length=200)
    description     = models.TextField(blank=True)
    status          = models.CharField(choices=Status, default=Status.DRAFT)
    trigger_type    = models.CharField(choices=TriggerType, default=TriggerType.BOTH)

    # Scheduling
    interval_minutes = models.PositiveIntegerField(null=True, blank=True)
    cron_expression  = models.CharField(max_length=100, null=True, blank=True)
    next_run         = models.DateTimeField(null=True, blank=True)
    last_run         = models.DateTimeField(null=True, blank=True)

    # LLM config
    ollama_model     = models.CharField(max_length=100, default='auto')
    system_prompt    = models.TextField()
    user_prompt_template = models.TextField(blank=True)

    # Communication
    sms_enabled      = models.BooleanField(default=True)
    email_enabled    = models.BooleanField(default=False)  # for reports only

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)
```

#### `AppConfig`
Generic key-value store for app-specific variables. Replaces YAML config entirely.

```python
class AppConfig(models.Model):

    class ValueType(models.TextChoices):
        STRING  = 'string'
        INTEGER = 'integer'
        BOOLEAN = 'boolean'
        SECRET  = 'secret'    # stored encrypted, masked in admin
        JSON    = 'json'

    app         = models.ForeignKey(App, on_delete=models.CASCADE, related_name='configs')
    key         = models.CharField(max_length=100)
    value       = models.TextField()
    value_type  = models.CharField(choices=ValueType, default=ValueType.STRING)
    description = models.CharField(max_length=255, blank=True)  # shown as help text in admin
    is_required = models.BooleanField(default=False)

    class Meta:
        unique_together = ('app', 'key')
```

#### `MCPServer`
MCP servers connected to an app.

```python
class MCPServer(models.Model):
    app         = models.ForeignKey(App, on_delete=models.CASCADE, related_name='mcp_servers')
    name        = models.CharField(max_length=100)
    url         = models.URLField()
    api_key     = models.TextField(blank=True)   # stored encrypted
    enabled     = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
```

#### `AppUser`
Which users are subscribed to / targeted by an app.

```python
class AppUser(models.Model):
    app         = models.ForeignKey(App, on_delete=models.CASCADE, related_name='app_users')
    user        = models.ForeignKey(User, on_delete=models.CASCADE)
    is_active   = models.BooleanField(default=True)
    joined_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('app', 'user')
```

---

### 4.3 `conversations` app

#### `Conversation`
One conversation thread per App+User pair.

```python
class Conversation(models.Model):
    app         = models.ForeignKey(App, on_delete=models.CASCADE)
    user        = models.ForeignKey(User, on_delete=models.CASCADE)
    started_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('app', 'user')
```

#### `Message`
Every message in a conversation — inbound and outbound.

```python
class Message(models.Model):

    class Role(models.TextChoices):
        SYSTEM    = 'system'
        USER      = 'user'       # inbound from the human
        ASSISTANT = 'assistant'  # outbound from the LLM

    class Channel(models.TextChoices):
        SMS   = 'sms'
        EMAIL = 'email'

    class Status(models.TextChoices):
        PENDING   = 'pending'
        SENT      = 'sent'
        DELIVERED = 'delivered'
        FAILED    = 'failed'
        RECEIVED  = 'received'

    conversation    = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role            = models.CharField(choices=Role)
    content         = models.TextField()
    channel         = models.CharField(choices=Channel)
    status          = models.CharField(choices=Status, default=Status.PENDING)
    is_report_request = models.BooleanField(default=False)  # flagged by intent detection
    aws_message_id  = models.CharField(max_length=255, blank=True)  # SNS/SES message ID
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

### 4.4 `reports` app

#### `Report`

```python
class Report(models.Model):

    class Status(models.TextChoices):
        GENERATING = 'generating'
        SENT       = 'sent'
        FAILED     = 'failed'

    conversation    = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    triggered_by    = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True)
    html_content    = models.TextField()      # LLM-generated HTML
    pdf_file        = models.BinaryField(null=True)   # WeasyPrint output
    status          = models.StatusField(choices=Status, default=Status.GENERATING)
    email_sent_to   = models.EmailField()
    aws_ses_id      = models.CharField(max_length=255, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

### 4.5 `scheduler` app

#### `AppRunLog`
Audit trail of every time an app's scheduled task ran.

```python
class AppRunLog(models.Model):

    class Result(models.TextChoices):
        SUCCESS = 'success'
        SKIPPED = 'skipped'    # user paused, or no users active
        FAILED  = 'failed'

    app         = models.ForeignKey(App, on_delete=models.CASCADE, related_name='run_logs')
    started_at  = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    result      = models.CharField(choices=Result)
    error       = models.TextField(blank=True)
    messages_sent = models.PositiveIntegerField(default=0)
```

---

## 5. Celery Architecture

### 5.1 Master Poller Task
Runs every minute via Celery Beat.

```python
@shared_task
def master_poller():
    now = timezone.now()
    due_apps = App.objects.filter(
        status=App.Status.ACTIVE,
        trigger_type__in=[App.TriggerType.SCHEDULED, App.TriggerType.BOTH],
        next_run__lte=now
    ).select_related('owner')

    for app in due_apps:
        app_runner.delay(app.id)
        app.next_run = calculate_next_run(app)
        app.last_run = now
        app.save(update_fields=['next_run', 'last_run'])
```

### 5.2 App Runner Task
One task per app execution. Handles the full outbound cycle.

```python
@shared_task
def app_runner(app_id):
    app = App.objects.get(id=app_id)
    active_users = app.app_users.filter(is_active=True).select_related('user')

    for app_user in active_users:
        process_app_for_user.delay(app_id, app_user.user_id)

@shared_task
def process_app_for_user(app_id, user_id):
    # 1. Load app + user + conversation history
    # 2. Build LLM context (system prompt + recent messages)
    # 3. Call Ollama
    # 4. Send SMS via AWS SNS
    # 5. Store outbound Message to DB
    # 6. Log to AppRunLog
```

### 5.3 Inbound SMS Consumer
Polls SQS for incoming messages from AWS Pinpoint.

```python
@shared_task
def sqs_consumer():
    # Runs every 30 seconds via Celery Beat
    messages = poll_sqs_queue()
    for msg in messages:
        process_inbound_sms.delay(msg)

@shared_task
def process_inbound_sms(payload):
    # 1. Extract phone number + message body
    # 2. Match to User + App via phone number
    # 3. Store inbound Message to DB
    # 4. Run intent detection via Ollama
    # 5. If report intent → report_generator.delay(conversation_id, message_id)
    # 6. Else → generate reply via Ollama → send SMS → store to DB
```

### 5.4 Report Generator Task

```python
@shared_task
def report_generator(conversation_id, triggered_by_message_id):
    # 1. Load full conversation history
    # 2. Call Ollama: generate HTML report
    # 3. WeasyPrint: HTML → PDF bytes
    # 4. Store Report record to DB
    # 5. Send PDF via AWS SES as attachment
    # 6. Send SMS confirmation: "Your report has been sent to [email]"
```

---

## 6. AWS Integration

### 6.1 Outbound SMS — AWS SNS
```python
# core/aws.py
import boto3

sns = boto3.client('sns', region_name=settings.AWS_REGION)

def send_sms(phone_number: str, message: str) -> str:
    response = sns.publish(
        PhoneNumber=phone_number,
        Message=message,
        MessageAttributes={
            'AWS.SNS.SMS.SMSType': {
                'DataType': 'String',
                'StringValue': 'Transactional'
            }
        }
    )
    return response['MessageId']
```

### 6.2 Inbound SMS — AWS Pinpoint → SNS → SQS
Setup (one-time, in AWS console):
1. Create Pinpoint project + buy a long code number
2. Create SNS topic for inbound messages
3. Create SQS queue subscribed to that SNS topic
4. Point Pinpoint's two-way SMS to the SNS topic

Django polls SQS via the `sqs_consumer` Celery task.

### 6.3 Outbound Email — AWS SES
```python
ses = boto3.client('ses', region_name=settings.AWS_REGION)

def send_email_with_pdf(to_email: str, subject: str, body: str, pdf_bytes: bytes, filename: str):
    # Uses SES raw email with MIME attachment
```

---

## 7. Intent Detection

Before processing any inbound SMS, Ollama classifies the user's intent:

```python
INTENT_PROMPT = """
Classify this message into one of these intents:
- journal_entry: user is writing a journal entry or responding to a prompt
- report_request: user wants a summary, report, or overview
- command: user is giving a command (!pause, !goals, etc.)
- question: user is asking a specific question
- other: anything else

Message: "{message}"
Reply with ONLY the intent label, nothing else.
"""
```

This runs as a fast, low-token Ollama call before the main processing logic.

---

## 8. Django Admin Configuration

The admin panel is the primary UI. Key customizations:

- **App admin**: inline `AppConfig`, `MCPServer`, and `AppUser` editors on the App detail page
- **AppConfig**: `secret` type values are masked with `***` in the list view, shown as password fields in edit
- **Conversation admin**: read-only message thread viewer (like a chat log)
- **AppRunLog admin**: filterable by app, result, date — useful for debugging
- **Report admin**: preview HTML, download PDF, resend email action

### Admin Permissions
- `is_platform_admin` → can see and manage all apps and users
- Regular staff → can only see apps they own
- Enforced via custom `ModelAdmin.get_queryset()` overrides

---

## 9. Settings Structure

```python
# settings.py (key additions)

AWS_REGION = env('AWS_REGION', default='us-east-1')
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY')
AWS_SNS_SMS_SENDER_ID = env('AWS_SNS_SMS_SENDER_ID', default='')
AWS_PINPOINT_APP_ID = env('AWS_PINPOINT_APP_ID')
AWS_SQS_INBOUND_URL = env('AWS_SQS_INBOUND_URL')
AWS_SES_FROM_EMAIL = env('AWS_SES_FROM_EMAIL')

OLLAMA_BASE_URL = env('OLLAMA_BASE_URL', default='http://localhost:11434')
OLLAMA_DEFAULT_MODEL = env('OLLAMA_DEFAULT_MODEL', default='auto')

CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

ENCRYPTION_KEY = env('ENCRYPTION_KEY')  # for encrypting AppConfig secrets
```

---

## 10. Tech Stack Summary

| Concern | Technology |
|---------|-----------|
| Web framework | Django 5.x |
| Admin UI | Django Admin (customized) |
| Task queue | Celery 5.x |
| Beat scheduler | django-celery-beat (DB scheduler) |
| Message broker | Redis |
| Database | PostgreSQL (platform DB) + SQLite per app (optional) |
| Outbound SMS | AWS SNS |
| Inbound SMS | AWS Pinpoint → SNS → SQS |
| Outbound email | AWS SES |
| LLM | Ollama (local) |
| PDF generation | WeasyPrint |
| Secret encryption | cryptography (Fernet) |
| Environment config | django-environ |
| AWS SDK | boto3 |

---

## 11. Build Phases (High Level)

| Phase | Deliverable |
|-------|-------------|
| 1 | Django project setup, all models, admin registration, migrations |
| 2 | Ollama client + basic LLM call working |
| 3 | Outbound SMS via AWS SNS working |
| 4 | Master poller + app runner Celery tasks |
| 5 | Inbound SMS via Pinpoint → SQS → Celery consumer |
| 6 | Intent detection + full conversation loop |
| 7 | Report generation (HTML → PDF → SES email) |
| 8 | Django admin polish + secret encryption |
| 9 | First real app built on top: Journaling Agent |
| 10 | Hardening, error handling, logging |
