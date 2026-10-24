"""jsonschema, marshmallow, Faker, phonenumbers and email-validator."""

import faker
import phonenumbers
from email_validator import EmailNotValidError, validate_email
from jsonschema import Draft202012Validator, FormatChecker
from marshmallow import Schema, ValidationError, fields, validate
from phonenumbers import carrier, geocoder
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def schema_checks() -> None:
    schema = {
        "type": "object",
        "properties": {
            "born": {"type": "string", "format": "date"},
            "mail": {"type": "string", "format": "email"},
            "ip": {"type": "string", "format": "ipv4"},
            "meta": {"$ref": "https://json-schema.org/draft/2020-12/schema"},
        },
        "required": ["born", "name"],
    }
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    instance = {"born": "2024-13-45", "mail": "nobody", "ip": "999.1.1.1", "meta": {"type": 12}}
    for message in sorted(error.message for error in validator.iter_errors(instance)):
        print("jsonschema", message)
    registry = Registry().with_resource(
        "urn:x", Resource.from_contents({"type": "integer"}, DRAFT202012)
    )
    print("jsonschema ref", Draft202012Validator({"$ref": "urn:x"}, registry=registry).is_valid(3))


class UserSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=2))
    age = fields.Int(validate=validate.Range(min=0, max=150))
    email = fields.Email()


def marshmallow_checks() -> None:
    schema = UserSchema()
    print("marshmallow load", schema.load({"name": "Ada", "age": "36", "email": "ada@example.com"}))
    try:
        schema.load({"name": "A", "age": 200, "email": "x"})
    except ValidationError as error:
        print("marshmallow errors", dict(sorted(error.messages.items())))
    print("marshmallow dump", schema.dump({"name": "Ada", "age": 36}))


def faker_checks() -> None:
    for locale in ("en_US", "de_DE", "hr_HR", "ja_JP"):
        faker.Faker.seed(0)
        fake = faker.Faker(locale)
        print("faker", locale, fake.name(), "|", fake.address().replace("\n", ", "))


def phone_checks() -> None:
    number = phonenumbers.parse("+41446681800")
    print("phone format",
          phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
    print("phone valid", phonenumbers.is_valid_number(number))
    print("phone geo", geocoder.description_for_number(number, "en"))
    print("phone carrier", repr(carrier.name_for_number(phonenumbers.parse("+447912345678"), "en")))
    print("phone de", phonenumbers.format_number(
        phonenumbers.parse("030 123456", "DE"), phonenumbers.PhoneNumberFormat.E164))


def email_checks() -> None:
    print("email", validate_email("Ada@Example.COM", check_deliverability=False).normalized)
    try:
        validate_email("ada@@example", check_deliverability=False)
    except EmailNotValidError as error:
        print("email invalid", error)


def main() -> None:
    schema_checks()
    marshmallow_checks()
    faker_checks()
    phone_checks()
    email_checks()
