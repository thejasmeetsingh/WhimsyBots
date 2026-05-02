from enum import Enum


class BaseChoices(Enum):
    @classmethod
    def get_values(cls):
        return tuple(x.value for x in cls)


class MCPTransportType(BaseChoices):
    REMOTE = ("R", "Remote")
    LOCAL = ("L", "Local")


class MessageRole(BaseChoices):
    SYSTEM = ("S", "System")
    USER = ("U", "User")
    ASSISTANT = ("A", "Assistant")


class MessageIntentType(BaseChoices):
    JOURNAL = ("J", "Journal Entry")
    REPORT = ("R", "Report Request")
    QUESTION = ("Q", "Question/Query")
    CRON_JOB = ("CJ", "Manage Cron Jobs")
    OTHER = ("O", "Other")
