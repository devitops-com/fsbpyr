"""boto3 with stubbed S3, azure blob SAS signing and google cloud storage, all offline."""

import datetime
import importlib.metadata
import io

import boto3
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
from botocore.stub import Stubber
from google.cloud import storage

# Azurite's published development account.
AZURITE = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


def s3() -> None:
    session = boto3.session.Session(
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake", region_name="eu-west-1"
    )
    client = session.client("s3")
    with Stubber(client) as stub:
        stub.add_response("list_buckets", {"Buckets": [{"Name": "alpha"}, {"Name": "beta"}]})
        stub.add_response("put_object", {"ETag": '"abc"'},
                          {"Bucket": "alpha", "Key": "k", "Body": b"data"})  # fmt: skip
        stub.add_response("get_object", {"Body": io.BytesIO(b"data"), "ContentLength": 4},
                          {"Bucket": "alpha", "Key": "k"})  # fmt: skip
        print("s3 buckets", [b["Name"] for b in client.list_buckets()["Buckets"]])
        print("s3 put", client.put_object(Bucket="alpha", Key="k", Body=b"data")["ETag"])
        print("s3 get", client.get_object(Bucket="alpha", Key="k")["Body"].read())
    print("boto services", len(session.get_available_services()))
    print("s3 regions", sorted(session.get_available_regions("s3"))[:5])


def azure() -> None:
    service = BlobServiceClient.from_connection_string(AZURITE)
    blob = service.get_blob_client("container", "dir/file.txt")
    print("azure url", blob.url)
    token = generate_blob_sas(
        account_name="devstoreaccount1", container_name="container", blob_name="dir/file.txt",
        account_key=service.credential.account_key, permission=BlobSasPermissions(read=True),
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        expiry=datetime.datetime(2024, 1, 2, tzinfo=datetime.UTC),
    )  # fmt: skip
    print("azure sas", token)


def google() -> None:
    client = storage.Client.create_anonymous_client()
    print("google public_url", client.bucket("bkt").blob("a b.txt").public_url)
    print("google top-level", sorted(importlib.metadata.packages_distributions()["google"]))
    for module in ("google.cloud", "google.api_core", "google.auth", "google.protobuf"):
        prefix = module.replace(".", "/") + "/"
        owners = {
            dist.metadata["Name"]
            for dist in importlib.metadata.distributions()
            if any(str(file).startswith(prefix) for file in dist.files or [])
        }
        print("google dist", module, sorted(owners))


def main() -> None:
    s3()
    azure()
    google()
