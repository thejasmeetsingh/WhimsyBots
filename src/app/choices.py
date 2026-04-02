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


class MessageChannel(BaseChoices):
    SMS = ("S", "SMS")
    EMAIL = ("E", "Email")


class MessageStatus(BaseChoices):
    PENDING = ("P", "Pending")
    SENT = ("S", "Sent")
    FAILED = ("F", "Failed")
    DELIVERED = ("D", "Delivered")
    RECEIVED = ("R", "Received")
