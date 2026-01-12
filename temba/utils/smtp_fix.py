"""
Monkey patch for Django 4.2.x compatibility with Python 3.12+
This fixes the SMTP.starttls() keyfile argument issue.
Remove this file once upgraded to Django 5.0+
"""
import sys

from django.core.mail.backends.smtp import EmailBackend as DjangoEmailBackend

if sys.version_info >= (3, 12):
    # Override the connection class to remove deprecated parameters
    class EmailBackend(DjangoEmailBackend):
        def open(self):
            """
            Ensure an open connection to the email server. Return whether or not a
            new connection was required (True or False) or None if an exception occurred.
            """
            if self.connection:
                # Nothing to do if the connection is already open.
                return False

            connection_params = {"timeout": self.timeout} if self.timeout else {}
            try:
                self.connection = self.connection_class(self.host, self.port, **connection_params)

                # TLS/SSL are mutually exclusive, so only attempt TLS over
                # non-secure connections.
                if not self.use_ssl and self.use_tls:
                    # Remove keyfile and certfile parameters for Python 3.12+
                    self.connection.starttls(context=self.ssl_context)

                if self.username and self.password:
                    self.connection.login(self.username, self.password)
                return True
            except OSError:
                if not self.fail_silently:
                    raise

    # Apply the patch
    import django.core.mail.backends.smtp

    django.core.mail.backends.smtp.EmailBackend = EmailBackend
