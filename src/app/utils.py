from croniter import croniter
from django.utils import timezone


def calculate_next_run_at(interval_mins, cron_expression):
    current_dt = timezone.now()

    if interval_mins:
        return current_dt + timezone.timedelta(minutes=interval_mins)

    return croniter(cron_expression, current_dt).get_next(timezone.datetime)


def get_admin_link(model, value, lookup, obj):
    if not obj or not value:
        return value    
    return f"<a href='/admin/app/{model}/?{lookup}={str(obj.id)}' target='_blank'>{value}</a>"
