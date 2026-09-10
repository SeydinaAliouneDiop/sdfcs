"""
Genere le hash bcrypt a coller dans AUTH_PASSWORD_HASH (.env).
Usage : python generate_password_hash.py
"""
from getpass import getpass
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

if __name__ == "__main__":
    password = getpass("Nouveau mot de passe admin : ")
    confirm = getpass("Confirmer : ")

    if password != confirm:
        print("Les deux mots de passe ne correspondent pas.")
    else:
        print("\nColle cette ligne dans ton .env :\n")
        print(f"AUTH_PASSWORD_HASH={pwd_context.hash(password)}")