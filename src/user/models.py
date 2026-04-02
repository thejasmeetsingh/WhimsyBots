import uuid

from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager as BaseUserManager


class UserManager(BaseUserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must set is_staff as True.")
        if extra_fields.get('is_superuser') is not True:
            raise ValueError("Superuser must set is_superuser as True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True, db_index=True, editable=False)
    username = None
    email = models.EmailField(unique=True, db_index=True)
    mobile_number = models.CharField(max_length=12, unique=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["mobile_number"]

    class Meta:
        ordering = ("email",)
        verbose_name_plural = "Users"
        verbose_name = "User"

    def validate_unique(self, exclude=None):
        if not self.email.islower():
            self.email = self.email.lower()
        super().validate_unique(exclude=["id"])
