import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from twilio.rest import Client

# 1. Configuración de credenciales de Twilio
twilio_phone_number = 'whatsapp:+14155238886'

account_sid = os.environ.get('account_sid')
auth_token = os.environ.get('au')
if not auth_token or not account_sid:
    try:
        import bridges
        auth_token = auth_token or bridges.au
        account_sid = account_sid or bridges.account_sid
    except ImportError:
        print("Advertencia: No se encontró la variable de entorno 'au'/'account_sid' ni el archivo 'bridges.py'")

# Inicializamos el cliente de Twilio
client = Client(account_sid, auth_token)

app = FastAPI()

# 2. Definimos la estructura de datos que esperamos recibir
class MensajeRequest(BaseModel):
    numero: str
    mensaje: str

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "API is running!"}

# 3. Endpoint principal para enviar mensajes de texto libre
@app.post("/enviar_mensaje")
def enviar_mensaje(req: MensajeRequest):
    try:
        # Asegurarnos de que el número tenga el formato de WhatsApp de Twilio
        numero_destino = req.numero
        # Agregar el código de país si no está presente
        if not numero_destino.startswith("+"):
            numero_destino = f"+52{numero_destino}"
        if not numero_destino.startswith("whatsapp:"):
            numero_destino = f"whatsapp:{numero_destino}"

        # Enviamos el mensaje usando Twilio
        message = client.messages.create(
            from_=twilio_phone_number,
            body=req.mensaje,
            to=numero_destino
        )
        
        # Imprimir toda la información en terminal
        print("\n" + "=" * 60)
        print("INFORMACIÓN COMPLETA DEL MENSAJE (TWILIO)")
        print("=" * 60)
        print(f"SID: {message.sid}")
        print(f"Account SID: {message.account_sid}")
        print(f"From: {message.from_}")
        print(f"To: {message.to}")
        print(f"Body: {message.body}")
        print(f"Status: {message.status}")
        print(f"Date Created: {message.date_created}")
        print(f"Date Sent: {message.date_sent}")
        print(f"Price: {message.price}")
        print(f"Price Unit: {message.price_unit}")
        print(f"Direction: {message.direction}")
        print(f"Num Segments: {message.num_segments}")
        print(f"Error Code: {message.error_code}")
        print(f"Error Message: {message.error_message}")
        print(f"API Version: {message.api_version}")
        print(f"URI: {message.uri}")
        print("=" * 60 + "\n")
        
        return {
            "status": "success", 
            "message_sid": message.sid,
            "info": f"Mensaje enviado a {numero_destino}"
        }
    except Exception as e:
        # Si Twilio marca error (ej. número inválido), regresamos error 500
        raise HTTPException(status_code=500, detail=str(e))
