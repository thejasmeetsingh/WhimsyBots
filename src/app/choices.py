from enum import Enum


class BaseChoices(Enum):
    @classmethod
    def get_values(cls):
        return tuple(x.value for x in cls)

    @classmethod
    def get_readable(cls, code: str) -> str:
        for member in cls:
            if member.value[0] == code:
                return member.value[1]
        raise ValueError(f"Code '{code}' not found in {cls.__name__}")


class MCPTransportType(BaseChoices):
    REMOTE = ("R", "Remote")
    LOCAL = ("L", "Local")


class MessageRole(BaseChoices):
    SYSTEM = ("S", "System")
    USER = ("U", "User")
    ASSISTANT = ("A", "Assistant")
