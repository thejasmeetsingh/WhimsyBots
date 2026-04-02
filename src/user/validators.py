import phonenumbers
from django.core.exceptions import ValidationError


def validate_mobile_number(value: str):
    if not value:
        raise ValidationError("Mobile number cannot be empty.")
    
    try:
        # Try to parse the phone number
        parsed_number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as e:
        raise ValidationError(f"Invalid phone number format: {str(e)}")
    
    # Check if the number is valid
    if not phonenumbers.is_valid_number(parsed_number):
        raise ValidationError("Invalid phone number.")
    
    # Check if it's a possible mobile number
    # This checks if the number type could be a mobile number
    number_type = phonenumbers.number_type(parsed_number)
    if number_type not in (
        phonenumbers.PhoneNumberType.MOBILE,
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    ):
        raise ValidationError("Phone number must be a valid mobile number.")
