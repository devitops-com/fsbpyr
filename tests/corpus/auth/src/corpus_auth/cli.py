"""Authentication and crypto: JWT, password hashes, NaCl, JWS, signed cookies, SSH."""

import socket
import threading

import argon2
import bcrypt
import itsdangerous
import jwt
import nacl.secret
import nacl.signing
import nacl.utils
import paramiko
from authlib.jose import JsonWebSignature
from cryptography.hazmat.primitives.asymmetric import ec, rsa

SECRET = b"corpus-secret-key-of-32-bytes!!!"


def tokens() -> None:
    token = jwt.encode({"sub": "ada", "admin": True}, SECRET, algorithm="HS256")
    print("HS256", token)
    print("HS256 verify", jwt.decode(token, SECRET, algorithms=["HS256"]))
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rs = jwt.encode({"sub": "ada"}, rsa_key, algorithm="RS256")
    print("RS256 verify", jwt.decode(rs, rsa_key.public_key(), algorithms=["RS256"]))
    ec_key = ec.generate_private_key(ec.SECP256R1())
    es = jwt.encode({"sub": "ada"}, ec_key, algorithm="ES256")
    print("ES256 verify", jwt.decode(es, ec_key.public_key(), algorithms=["ES256"]))


def passwords() -> None:
    hashed = bcrypt.hashpw(b"hunter2", bcrypt.gensalt(rounds=4))
    print("bcrypt", bcrypt.checkpw(b"hunter2", hashed), bcrypt.checkpw(b"wrong", hashed))
    hasher = argon2.PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    encoded = hasher.hash("hunter2")
    print("argon2", hasher.verify(encoded, "hunter2"), encoded.startswith("$argon2id$"))


def nacl_and_signing() -> None:
    signing = nacl.signing.SigningKey(bytes(range(32)))
    signed = signing.sign(b"message")
    print("ed25519", signed.signature.hex())
    print("ed25519 verify", signing.verify_key.verify(signed) == b"message")
    box = nacl.secret.SecretBox(nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE))
    print("secretbox", box.decrypt(box.encrypt(b"secret")))
    jws = JsonWebSignature(["HS256"]).serialize_compact({"alg": "HS256"}, b"payload", SECRET)
    print("authlib jws", JsonWebSignature(["HS256"]).deserialize_compact(jws, SECRET)["payload"])
    serializer = itsdangerous.URLSafeSerializer("fixed-key", salt="corpus")
    cookie = serializer.dumps({"user": 7})
    print("itsdangerous", cookie, serializer.loads(cookie))


class Server(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        def answer():
            channel.send(b"ran " + command + b"\n")
            channel.send_exit_status(0)
            channel.shutdown_write()  # end of output; the server closes the channel later, after
            # the reply to the exec request, which a close sent before it would turn into an error

        threading.Thread(target=answer).start()
        return True


def ssh(key: paramiko.RSAKey) -> None:
    signature = key.sign_ssh_data(b"message").asbytes()
    verified = [
        key.verify_ssh_sig(data, paramiko.Message(signature)) for data in (b"message", b"other")
    ]
    print("paramiko verify", verified)
    server_socket, client_socket = socket.socketpair()
    transport = paramiko.Transport(server_socket)
    transport.add_server_key(key)
    accepted = []

    def serve() -> None:  # the key exchange needs the client to run at the same time
        transport.start_server(server=Server())
        accepted.append(transport.accept(10))

    starter = threading.Thread(target=serve)
    starter.start()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("corpus", username="ada", password="x", sock=client_socket,
                   allow_agent=False, look_for_keys=False)
    _, stdout, _ = client.exec_command("hostname")
    print("ssh exec", stdout.read().decode().strip(), stdout.channel.recv_exit_status())
    starter.join()
    accepted[0].close()
    client.close()
    transport.close()


def main() -> None:
    tokens()
    passwords()
    nacl_and_signing()
    ssh(paramiko.RSAKey.generate(2048))
