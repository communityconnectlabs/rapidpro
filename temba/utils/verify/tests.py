from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from .utils import EmailVerification, PhoneVerification, Verification


class MockUserSettings:
    """Mock object for user settings"""

    def __init__(self, verification_type, tel=None):
        self.verification_type = verification_type
        self.tel = tel


class VerificationMetaTest(TestCase):
    """Test the metaclass registration and selection logic"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_phone_verification_is_registered(self):
        """Test that PhoneVerification class is registered with correct type"""
        user_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.PHONE, tel="+1234567890")

        with patch("temba.utils.verify.utils.get_verification_service"):
            verification = Verification(user=self.user, user_settings=user_settings)

        self.assertIsInstance(verification, PhoneVerification)
        self.assertEqual(verification.user_settings, user_settings)

    def test_email_verification_is_registered(self):
        """Test that EmailVerification class is registered with correct type"""
        user_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.EMAIL)

        with patch("temba.utils.verify.utils.get_verification_service"):
            verification = Verification(user=self.user, user_settings=user_settings)

        self.assertIsInstance(verification, EmailVerification)
        self.assertEqual(verification.user, self.user)

    def test_raises_error_for_unregistered_verification_type(self):
        """Test that creating verification with unregistered type raises error"""
        user_settings = MockUserSettings(verification_type=999)  # Invalid type

        with self.assertRaises(ValueError) as context:
            Verification(user=self.user, user_settings=user_settings)

        self.assertIn("Unknown verification type", str(context.exception))

    def test_raises_error_for_none_user_settings(self):
        """Test that None user_settings raises assertion error"""
        with self.assertRaises(AssertionError) as context:
            Verification(user=self.user, user_settings=None)

        self.assertIn("User settings cannot be None", str(context.exception))

    def test_raises_error_for_missing_verification_type(self):
        """Test that user_settings without verification_type raises error"""

        class InvalidUserSettings:
            pass

        with self.assertRaises(AssertionError) as context:
            Verification(user=self.user, user_settings=InvalidUserSettings())

        self.assertIn("verification_type", str(context.exception))


class PhoneVerificationTest(TestCase):
    """Test PhoneVerification implementation"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser")
        self.user_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.PHONE, tel="+1234567890")

    def test_phone_verification_start(self):
        """Test that start_verification calls the service correctly"""
        mock_service = Mock()

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            verification = PhoneVerification(self.user, self.user_settings)
            verification.start_verification()

        mock_service.verifications.create.assert_called_once_with(
            to=self.user_settings.tel,
            channel="sms",
        )

    def test_phone_verification_complete_approved(self):
        """Test complete_verification returns True when approved"""
        mock_service = Mock()
        mock_result = Mock(status="approved")
        mock_service.verification_checks.create.return_value = mock_result

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            verification = PhoneVerification(self.user, self.user_settings)
            result = verification.complete_verification("123456")

        self.assertTrue(result)
        mock_service.verification_checks.create.assert_called_once_with(
            to=self.user_settings.tel,
            code="123456",
        )

    def test_phone_verification_complete_denied(self):
        """Test complete_verification returns False when not approved"""
        mock_service = Mock()
        mock_result = Mock(status="denied")
        mock_service.verification_checks.create.return_value = mock_result

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            verification = PhoneVerification(self.user, self.user_settings)
            result = verification.complete_verification("123456")

        self.assertFalse(result)


class EmailVerificationTest(TestCase):
    """Test EmailVerification implementation"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")
        self.user_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.EMAIL)

    def test_email_verification_start(self):
        """Test that start_verification calls the service correctly"""
        mock_service = Mock()

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            verification = EmailVerification(self.user, self.user_settings)
            verification.start_verification()

        mock_service.verifications.create.assert_called_once_with(
            to=self.user.email,
            channel="email",
        )

    def test_email_verification_complete_approved(self):
        """Test complete_verification returns True when approved"""
        mock_service = Mock()
        mock_result = Mock(status="approved")
        mock_service.verification_checks.create.return_value = mock_result

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            verification = EmailVerification(self.user, self.user_settings)
            result = verification.complete_verification("123456")

        self.assertTrue(result)
        mock_service.verification_checks.create.assert_called_once_with(
            to=self.user.email,
            code="123456",
        )

    def test_email_verification_complete_denied(self):
        """Test complete_verification returns False when not approved"""
        mock_service = Mock()
        mock_result = Mock(status="pending")
        mock_service.verification_checks.create.return_value = mock_result

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            verification = EmailVerification(self.user, self.user_settings)
            result = verification.complete_verification("999999")

        self.assertFalse(result)


class VerificationHelperFunctionsTest(TestCase):
    """Test start_user_verification and complete_user_verification helper functions"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_start_user_verification_phone(self):
        """Test start_user_verification works with phone verification"""
        mock_service = Mock()
        mock_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.PHONE, tel="+1234567890")

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            with patch.object(self.user, "get_settings", return_value=mock_settings):
                from .utils import start_user_verification

                start_user_verification(self.user)

        mock_service.verifications.create.assert_called_once_with(
            to=mock_settings.tel,
            channel="sms",
        )

    def test_start_user_verification_email(self):
        """Test start_user_verification works with email verification"""
        mock_service = Mock()
        mock_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.EMAIL)

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            with patch.object(self.user, "get_settings", return_value=mock_settings):
                from .utils import start_user_verification

                start_user_verification(self.user)

        mock_service.verifications.create.assert_called_once_with(
            to=self.user.email,
            channel="email",
        )

    def test_complete_user_verification_success(self):
        """Test complete_user_verification returns True on success"""
        mock_service = Mock()
        mock_result = Mock(status="approved")
        mock_service.verification_checks.create.return_value = mock_result
        mock_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.PHONE, tel="+1234567890")

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            with patch.object(self.user, "get_settings", return_value=mock_settings):
                from .utils import complete_user_verification

                result = complete_user_verification(self.user, "123456")

        self.assertTrue(result)

    def test_complete_user_verification_failure(self):
        """Test complete_user_verification returns False on failure"""
        mock_service = Mock()
        mock_result = Mock(status="denied")
        mock_service.verification_checks.create.return_value = mock_result
        mock_settings = MockUserSettings(verification_type=settings.VERIFICATION_TYPES.EMAIL)

        with patch("temba.utils.verify.utils.get_verification_service", return_value=mock_service):
            with patch.object(self.user, "get_settings", return_value=mock_settings):
                from .utils import complete_user_verification

                result = complete_user_verification(self.user, "999999")

        self.assertFalse(result)
