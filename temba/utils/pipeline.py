from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.http import HttpResponseRedirect
from django.urls import reverse


def require_pre_registered_user(strategy, details, backend, user=None, *args, **kwargs):
    """
    Prevent users from logging in if they are not pre-registered in the Django User model.
    """
    email = details.get("email")

    if not email:
        messages.error(strategy.request, "Access denied. No email provided by Microsoft.")
        return HttpResponseRedirect(reverse("users.user_login"))

    if not get_user_model().objects.filter(email=email).exists():
        messages.error(strategy.request, "Access denied. You are not pre-registered.")
        return HttpResponseRedirect(reverse("users.user_login"))

    return {"is_new": False}


def associate_by_email(backend, details, user=None, *args, **kwargs):
    """
    Associate the social auth user with an existing user by email.
    """
    if user:
        return {"user": user}

    email = details.get("email")
    if email:
        try:
            user = get_user_model().objects.get(email=email)
            return {"user": user}
        except User.DoesNotExist:
            pass

    return None
