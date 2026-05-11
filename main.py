import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from twilio.rest import Client

# Selecciona el ambiente: "dev" o "prod".
# Cambia el default aquí, o exporta APP_ENV antes de arrancar el servidor.
APP_ENV = os.environ.get("APP_ENV", "dev")

load_dotenv(Path(__file__).parent / f".env.{APP_ENV}")

# 1. Configuración de credenciales de Twilio
twilio_phone_number = 'whatsapp:+14155238886'

account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

if not account_sid or not auth_token:
    raise RuntimeError(
        f"Faltan credenciales en .env.{APP_ENV}. "
        "Define TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN."
    )

# Inicializamos el cliente de Twilio
client = Client(account_sid, auth_token)

app = FastAPI()

# Bus en memoria para SSE. Cada cliente conectado a /events tiene su propia cola.
# Si la cola se llena (cliente lento), se descarta para no bloquear la API.
_subscribers: set[asyncio.Queue] = set()


async def _publish(event: dict):
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


@app.middleware("http")
async def log_calls(request: Request, call_next):
    # No logueamos el propio stream para no auto-alimentar a los clientes.
    if request.url.path == "/events":
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "client": request.client.host if request.client else None,
    }
    summary = getattr(request.state, "summary", None)
    if summary:
        event["summary"] = summary
    await _publish(event)
    return response


# 2. Definimos la estructura de datos que esperamos recibir
class MensajeRequest(BaseModel):
    numero: str
    mensaje: str

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "API is running!"}

# 3. Endpoint principal para enviar mensajes de texto libre
@app.post("/enviar_mensaje")
def enviar_mensaje(req: MensajeRequest, request: Request):
    try:
        # Asegurarnos de que el número tenga el formato de WhatsApp de Twilio
        numero_destino = req.numero
        # Agregar el código de país si no está presente
        if not numero_destino.startswith("+"):
            numero_destino = f"+52{numero_destino}"
        if not numero_destino.startswith("whatsapp:"):
            numero_destino = f"whatsapp:{numero_destino}"

        # Resumen para el stream SSE (sin cuerpo del mensaje ni credenciales)
        request.state.summary = f"to={numero_destino}"

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


# 4. Webhook que recibe mensajes entrantes de WhatsApp desde Twilio
@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    form_data = await request.form()
    from_number = form_data.get("From")
    body = form_data.get("Body")
    message_sid = form_data.get("MessageSid")

    print("\n" + "=" * 60)
    print("WEBHOOK /webhook/whatsapp - MENSAJE ENTRANTE")
    print("=" * 60)
    print(f"De: {from_number}")
    print(f"Mensaje: {body}")
    print(f"SID: {message_sid}")

    num_media = int(form_data.get("NumMedia", "0") or 0)
    if num_media > 0:
        for i in range(num_media):
            media_url = form_data.get(f"MediaUrl{i}")
            media_type = form_data.get(f"MediaContentType{i}")
            print(f"  Adjunto {i}: {media_type} - {media_url}")
    print("=" * 60 + "\n")

    # Twilio espera 200 con cuerpo vacío para confirmar la recepción
    return {}


# 5. Webhook que recibe actualizaciones de estado de mensajes enviados
@app.post("/webhook/status")
async def webhook_status(request: Request):
    form_data = await request.form()
    message_sid = form_data.get("MessageSid")
    message_status = form_data.get("MessageStatus")
    to_number = form_data.get("To")

    print("\n" + "=" * 60)
    print("WEBHOOK /webhook/status - ACTUALIZACIÓN DE ESTADO")
    print("=" * 60)
    print(f"SID: {message_sid}")
    print(f"Estado: {message_status}")
    print(f"Para: {to_number}")
    print("=" * 60 + "\n")

    return {}


# 6. Stream SSE de todos los requests que recibe la API.
# El frontend se conecta a /events y recibe en vivo cada llamada (excepto /events).
@app.get("/events")
async def events(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(queue)

    async def gen():
        try:
            # Heartbeat inicial para abrir el stream cuanto antes
            yield ": ok\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(evt)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive cada 15s para que proxies no cierren la conexión
                    yield ": ping\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
