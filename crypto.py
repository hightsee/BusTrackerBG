import base64
import urllib.parse
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from config import AES_KEY_B64, AES_IV_B64

def get_aes_cipher():
    key = base64.b64decode(AES_KEY_B64)
    iv = base64.b64decode(AES_IV_B64)
    return AES.new(key, AES.MODE_CBC, iv)

def encrypt_payload(data_str):
    cipher = get_aes_cipher()
    padded_data = pad(data_str.encode('utf-8'), AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_data)
    encrypted_b64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    return urllib.parse.quote(encrypted_b64)

def decrypt_response(encrypted_str):
    url_decoded = urllib.parse.unquote(encrypted_str)
    encrypted_bytes = base64.b64decode(url_decoded)
    cipher = get_aes_cipher()
    decrypted_padded = cipher.decrypt(encrypted_bytes)
    decrypted_bytes = unpad(decrypted_padded, AES.block_size)
    return decrypted_bytes.decode('utf-8')

