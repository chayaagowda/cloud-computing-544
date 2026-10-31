from botocore.exceptions import ClientError
from create_database import main

try:
    main()
except ClientError as error:
    print("Operation:", error.operation_name)
    print("Code:", error.response["Error"]["Code"])
    print("Message:", error.response["Error"]["Message"])