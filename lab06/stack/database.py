"""Private PostgreSQL infrastructure for the weather service."""

from .network import find_security_group
from .util import ignore_errors


def database_ingress(settings) -> list[dict]:
    """Allow PostgreSQL only from the application security group."""
    return [{
        "IpProtocol": "tcp",
        "FromPort": settings.db_port,
        "ToPort": settings.db_port,
        "UserIdGroupPairs": [{"GroupId": settings.app_sg_id}],
    }]


def ensure_database_security_group(aws, settings, vpc_id: str) -> str:
    """Create or reuse the database security group and its ingress rule."""
    group_id = find_security_group(aws, settings.db_sg_name, vpc_id)
    if group_id is None:
        group_id = aws.ec2.create_security_group(
            GroupName=settings.db_sg_name,
            Description="PostgreSQL access from the weather application",
            VpcId=vpc_id,
        )["GroupId"]

    with ignore_errors("InvalidPermission.Duplicate"):
        aws.ec2.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=database_ingress(settings),
        )
    return group_id


def database_request(settings, group_id: str, password: str) -> dict:
    """Build the RDS create request; never log this returned dictionary."""
    return {
        "DBInstanceIdentifier": settings.db_instance_id,
        "DBInstanceClass": settings.db_instance_class,
        "Engine": "postgres",
        "AllocatedStorage": settings.db_allocated_storage,
        "StorageType": "gp2",
        "StorageEncrypted": True,
        "MultiAZ": False,
        "PubliclyAccessible": False,
        "BackupRetentionPeriod": 0,
        "DeletionProtection": False,
        "CopyTagsToSnapshot": False,
        "DBName": settings.db_name,
        "MasterUsername": settings.db_user,
        "MasterUserPassword": password,
        "Port": settings.db_port,
        "VpcSecurityGroupIds": [group_id],
        "DBSubnetGroupName": settings.db_subnet_group_name,
        "Tags": [{"Key": "Name", "Value": settings.db_instance_id}],
    }


def find_database(aws, settings) -> dict | None:
    """Find this lab's database, returning None only when it is absent."""
    from botocore.exceptions import ClientError
    from .util import error_code

    try:
        response = aws.rds.describe_db_instances(
            DBInstanceIdentifier=settings.db_instance_id
        )
        return response["DBInstances"][0]
    except ClientError as error:
        if error_code(error) != "DBInstanceNotFound":
            raise
        return None


def ensure_database_password(aws, settings) -> str:
    """Reuse the SSM password or generate one without printing or saving it."""
    import secrets
    from botocore.exceptions import ClientError
    from .util import error_code

    try:
        return aws.ssm.get_parameter(
            Name=settings.db_password_param,
            WithDecryption=True,
        )["Parameter"]["Value"]
    except ClientError as error:
        if error_code(error) != "ParameterNotFound":
            raise

    if find_database(aws, settings) is not None:
        raise RuntimeError(
            "Database exists but its password parameter is missing. "
            "Refusing to generate a mismatched password."
        )

    password = secrets.token_hex(16)
    try:
        aws.ssm.put_parameter(
            Name=settings.db_password_param,
            Type="SecureString",
            Tier="Standard",
            Value=password,
            Overwrite=False,
        )
    except ClientError as error:
        if error_code(error) != "ParameterAlreadyExists":
            raise
        return aws.ssm.get_parameter(
            Name=settings.db_password_param,
            WithDecryption=True,
        )["Parameter"]["Value"]
    return password


def ensure_database_subnet_group(aws, settings, subnets: list[str]) -> None:
    """Create a database subnet group or validate the existing group."""
    from botocore.exceptions import ClientError
    from .util import error_code

    try:
        group = aws.rds.describe_db_subnet_groups(
            DBSubnetGroupName=settings.db_subnet_group_name
        )["DBSubnetGroups"][0]
    except ClientError as error:
        if error_code(error) != "DBSubnetGroupNotFoundFault":
            raise
    else:
        existing = {item["SubnetIdentifier"] for item in group["Subnets"]}
        if existing != set(subnets):
            raise RuntimeError("Existing database subnet group differs.")
        return

    aws.rds.create_db_subnet_group(
        DBSubnetGroupName=settings.db_subnet_group_name,
        DBSubnetGroupDescription="Weather database subnets in two AZs",
        SubnetIds=subnets,
    )


def ensure_database_instance(aws, settings, group_id: str) -> dict:
    """Create PostgreSQL once, wait until available, and return its metadata."""
    from botocore.exceptions import ClientError
    from .util import error_code

    password = ensure_database_password(aws, settings)
    existing = find_database(aws, settings)

    if existing is None:
        try:
            aws.rds.create_db_instance(
                **database_request(settings, group_id, password)
            )
        except ClientError as error:
            if error_code(error) != "DBInstanceAlreadyExists":
                raise
    elif existing["DBInstanceStatus"] == "deleting":
        raise RuntimeError("Database is still deleting; wait before recreating.")

    del password

    aws.rds.get_waiter("db_instance_available").wait(
        DBInstanceIdentifier=settings.db_instance_id,
        WaiterConfig={"Delay": 30, "MaxAttempts": 60},
    )
    return find_database(aws, settings)


def delete_database_resources(aws, settings, vpc_id: str) -> None:
    """Delete the database without a final snapshot or retained backups."""
    from .util import retry_on

    database = find_database(aws, settings)
    if database is not None:
        if database["DBInstanceStatus"] != "deleting":
            with ignore_errors("DBInstanceNotFound"):
                aws.rds.delete_db_instance(
                    DBInstanceIdentifier=settings.db_instance_id,
                    SkipFinalSnapshot=True,
                    DeleteAutomatedBackups=True,
                )

        aws.rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=settings.db_instance_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )

    # Keep the password until database deletion has completed.
    with ignore_errors("ParameterNotFound"):
        aws.ssm.delete_parameter(Name=settings.db_password_param)

    with ignore_errors("DBSubnetGroupNotFoundFault"):
        aws.rds.delete_db_subnet_group(
            DBSubnetGroupName=settings.db_subnet_group_name
        )

    group_id = find_security_group(aws, settings.db_sg_name, vpc_id)
    if group_id:
        with ignore_errors("InvalidGroup.NotFound"):
            retry_on(
                {"DependencyViolation"},
                lambda: aws.ec2.delete_security_group(GroupId=group_id),
                attempts=12,
                delay=10,
            )
