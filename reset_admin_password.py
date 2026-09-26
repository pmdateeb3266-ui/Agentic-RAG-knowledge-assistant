import getpass

from auth_manager import AuthError, log_security_event, reset_user_password
from storage import connect, initialize_database


def main():
    initialize_database(clean=False).close()
    connection = connect()
    try:
        admins = connection.execute(
            "SELECT id, username, display_name FROM users WHERE role='admin' AND enabled=1 ORDER BY username"
        ).fetchall()
    finally:
        connection.close()
    if not admins:
        print("No active administrator account exists. Open the web application to complete first-time setup.")
        return 1
    print("Active administrators: " + ", ".join(str(row["username"]) for row in admins))
    username = input("Administrator username: ").strip()
    selected = next((row for row in admins if str(row["username"]).casefold() == username.casefold()), None)
    if not selected:
        print("Administrator not found.")
        return 1
    password = getpass.getpass("New password (at least 10 characters): ")
    confirmation = getpass.getpass("Type the new password again: ")
    if password != confirmation:
        print("The passwords do not match.")
        return 1
    try:
        reset_user_password(int(selected["id"]), password)
    except AuthError as error:
        print(str(error))
        return 1
    log_security_event(
        {"id": int(selected["id"]), "username": str(selected["username"])},
        "local_admin_password_recovery", True,
    )
    print("Administrator password changed. Start the application and sign in with the new password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
