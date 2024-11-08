import logging
from io import BytesIO

import boto3
from botocore.exceptions import ClientError

from django.conf import settings
from django.core.management import BaseCommand

from celery import shared_task

from temba.utils.email import send_temba_email

logger = logging.getLogger(__name__)


@shared_task
def migrate_s3_buckets(
    source_access_key,
    source_secret_key,
    source_bucket_name,
    destination_access_key,
    destination_secret_key,
    destination_bucket_name,
    email_address=None,
):
    source_s3 = boto3.client("s3", aws_access_key_id=source_access_key, aws_secret_access_key=source_secret_key)
    destination_s3 = boto3.client(
        "s3", aws_access_key_id=destination_access_key, aws_secret_access_key=destination_secret_key
    )

    # Generator that returns pages with list of items to be copied
    def get_list_objects_pages():
        try:
            paginator = source_s3.get_paginator("list_objects_v2")
            pages_iterator = paginator.paginate(Bucket=source_bucket_name)
            for page in pages_iterator:
                yield page.get("Contents", [])
        except ClientError:
            logger.error("Failed to access to source s3 bucket, please, check provided credentials.")

    # Loop through the objects and copy them to the destination bucket
    pages = get_list_objects_pages()
    for page in pages:
        for content_item in page:
            source_key = content_item["Key"]
            try:
                destination_s3.head_object(Bucket=destination_bucket_name, Key=source_key)
                logger.info(f"Skipped: s3://{destination_bucket_name}/{source_key} already exists.")
            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    # Copy the object to the destination bucket
                    with BytesIO() as f:
                        try:
                            source_s3.download_fileobj(source_bucket_name, source_key, f)
                            f.seek(0)
                            destination_s3.upload_fileobj(f, destination_bucket_name, source_key)
                            logger.info(
                                f"Copied: s3://{source_bucket_name}/{source_key} to s3://{destination_bucket_name}/{source_key}"
                            )
                        except Exception as e:
                            logger.info(
                                f"Failed to copy: s3://{source_bucket_name}/{source_key} to s3://{destination_bucket_name}/{source_key}: {str(e)}"
                            )
                else:
                    logger.error("Failed to access to destination s3 bucket, please, check provided credentials.")
                    return
            except Exception as e:
                logger.error("Unknown error:", e)

    logger.info(f"Migration from {source_bucket_name} to {destination_bucket_name} completed.")

    if email_address:
        send_temba_email(
            subject="S3 migration finished",
            text="Your S3 bucket migration got finished",
            html=None,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email_address],
        )


class Command(BaseCommand):
    """
    This script will help you to migrate files from one bucket to another.
    To execute commant please run:

        python manage.py migrate_s3_buckets "AKIAW**** cFC4VuxIgu****** src_bucket_name" "GDSDJW**** Hwdwh2TW****** dest_bucket_name" "info@communityconnectlabs.com"
    """

    help = "Migrate files from one s3 bucket to another."

    def add_arguments(self, parser):
        parser.add_argument("source_credentials", type=str)
        parser.add_argument("destination_credentials", type=str)
        parser.add_argument("email_address", type=str)

    @staticmethod
    def prepare_credentials(creds_string: str):
        creds = list(map(lambda x: x.strip(), creds_string.split(" ")))
        return creds, len(creds) == 3

    def handle(self, *_, **options):
        src_creds, src_valid = self.prepare_credentials(options.get("source_credentials", ""))
        dest_creds, dest_valid = self.prepare_credentials(options.get("destination_credentials", ""))
        email_address = options.get("email_address", None)
        if not all([src_valid, dest_valid]):
            self.stderr.write(
                'Credentials provided incorrectly. Please provide each credentials in next format: "access_key secret_key bucket_name"'
            )
            return
        migrate_s3_buckets.delay(*src_creds, *dest_creds, email_address)
