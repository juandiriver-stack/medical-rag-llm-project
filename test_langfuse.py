from dotenv import load_dotenv
load_dotenv()

from langfuse import get_client, observe

langfuse = get_client()

ok = langfuse.auth_check()
print("auth_check:", ok)

if not ok:
    raise Exception("Credenciales de Langfuse inválidas o no cargadas")

@observe()
def prueba_langfuse():
    return "Hola mundo desde Langfuse"

resultado = prueba_langfuse()
print("Resultado:", resultado)

langfuse.flush()
print("✅ Test ejecutado")