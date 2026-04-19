from flask import Flask, request, jsonify
import requests
from datetime import datetime
import os

app = Flask(__name__)

ACCESS_TOKEN    = os.environ.get("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN    = os.environ.get("VERIFY_TOKEN")

# ── Números de proveedor por tienda ──────────────────────────
PROVEEDORES = {
    "carniceria": "5212464922368",
    "abarrotes":  "521XXXXXXXXXX",
    "farmacia":   "521XXXXXXXXXX",
}

# ── Estado por cliente ───────────────────────────────────────
clientes: dict[str, dict] = {}

def getStatus(sender: str) -> dict:
    if sender not in clientes:
        clientes[sender] = {
            "carrito": [],
            "esperando_cantidad": False,
            "item_en_curso": None,
            "esperando_ubicacion": False,
            "ubicacion": None,
        }
    return clientes[sender]


def resetStatus(sender: str):
    clientes.pop(sender, None)


# ── Helpers de envío ─────────────────────────────────────────

def sendReply(to_number: str, message: str):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message},
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    print(f"send_reply → {r.status_code} {r.json()}")
    return r


def sendListP(to_number: str, header_text: str, body_text: str,
                           sections: list, footer_text: str = "Te lo llevamos a domicilio",
                           button_text: str = "Ver opciones"):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header_text},
            "body": {"text": body_text},
            "footer": {"text": footer_text},
            "action": {"button": button_text, "sections": sections},
        },
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    print(f"send_list → {r.status_code} {r.json()}")
    return r


def sendButtonOptions(to_number: str, body_text: str, buttons: list):
    """Envía mensaje con botones de respuesta rápida."""
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    print(f"send_buttons → {r.status_code} {r.json()}")
    return r


def locationRequest(to_number: str, body_text: str = "¿Desde dónde te enviamos el pedido?"):
    """Solicita la ubicación del usuario."""
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "location_request_message",
            "body": {"text": body_text},
            "action": {"name": "send_location"},
        },
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    print(f"send_location_request → {r.status_code} {r.json()}")
    return r


# ── Lógica de carrito ────────────────────────────────────────

def summaryBuy(carrito: list) -> str:
    if not carrito:
        return "_(vacío)_"
    lineas = [f"  • {i['producto']} x{i['cantidad']} ({i['tienda'].capitalize()})"
              for i in carrito]
    return "\n".join(lineas)


def sendOptionsAgreeConfirm(sender: str):
    """Después de agregar un item muestra: Agregar más | Confirmar pedido."""
    estado = getStatus(sender)
    resumen = summaryBuy(estado["carrito"])
    sendButtonOptions(
        to_number=sender,
        body_text=f"🛒 *Tu pedido hasta ahora:*\n{resumen}\n\n¿Qué deseas hacer?",
        buttons=[
            {"id": "agregar_mas",      "title": "➕ Agregar más"},
            {"id": "confirmar_pedido", "title": "✅ Confirmar pedido"},
        ],
    )


def finalConfirmation(sender: str):
    """Primero solicita la ubicación; los botones Confirmar/Cancelar se muestran al recibirla."""
    estado = getStatus(sender)
    resumen = summaryBuy(estado["carrito"])
    estado["esperando_ubicacion"] = True
    sendReply(sender, f"📋 *Resumen de tu pedido:*\n{resumen}")
    locationRequest(sender, "📍 Por favor comparte tu ubicación para confirmar la entrega.")


def providerNotification(carrito: list, cliente: str):
    """Agrupa items por tienda y notifica a cada proveedor."""
    por_tienda: dict[str, list] = {}
    for item in carrito:
        por_tienda.setdefault(item["tienda"], []).append(item)

    for tienda, items in por_tienda.items():
        numero = PROVEEDORES.get(tienda)
        if not numero:
            print(f"❌ Sin número para: {tienda}")
            continue
        lineas = "\n".join(f"  • {i['producto']} — {i['cantidad']}" for i in items)
        mensaje = (
            f"🛒 *Nuevo pedido*\n"
            f"👤 Cliente: {cliente}\n"
            f"🏪 Tienda: {tienda.capitalize()}\n"
            f"📦 Productos:\n{lineas}\n"
        )
        sendReply(numero, mensaje)

def distributorNotification(carrito: list, cliente: str, ubicacion: dict | None = None):
    """Agrupa items por tienda y notifica al distribuidor con un solo mensaje."""
    por_tienda: dict[str, list] = {}
    for item in carrito:
        por_tienda.setdefault(item["tienda"], []).append(item)

    ubicacion_texto = (
        f"https://maps.google.com/?q={ubicacion['lat']},{ubicacion['lng']}"
        if ubicacion else "No proporcionada"
    )

    secciones = []
    for tienda, items in por_tienda.items():
        lineas = "\n".join(f"    • {i['producto']} — {i['cantidad']}" for i in items)
        secciones.append(f"🏪 *{tienda.capitalize()}*\n{lineas}")

    mensaje = (
        f"🛒 *Nuevo pedido*\n"
        f"👤 Cliente: {cliente}\n\n"
        + "\n\n".join(secciones)
        + f"\n\n📍 Ubicación: {ubicacion_texto}"
    )

    numero = "5212461780070"
    if not numero:
        print("❌ Sin número general para notificaciones")
        return

    sendReply(numero, mensaje)


# ── Menús ────────────────────────────────────────────────────

def sendPrincipalMenu(sender: str):
    sendListP(
        to_number=sender,
        header_text="🛍️ Bienvenido al Mercadito",
        body_text="¿Qué tienda quieres visitar?",
        sections=[{
            "title": "🛍️ Selecciona una opción",
            "rows": [
                {"id": "tienda1", "title": "🥩 Carnicería", "description": "¿Qué corte deseas hoy?"},
                {"id": "tienda2", "title": "🛒 Abarrotes",  "description": "¿Qué productos necesitas?"},
                {"id": "tienda3", "title": "💊 Farmacia",   "description": "¿Qué medicamento necesitas hoy?"},
            ],
        }],
    )

def sendStoreMenu(sender: str, tienda_id: str):
    menus = {
        "tienda1": dict(
            header_text="🥩 Carnicería",
            body_text="Selecciona el corte que deseas:",
            sections=[{"title": "🛍️ Te ofrecemos:", "rows": [
                {"id": "pc1", "title": "Bistec",    "description": "$180 kilo"},
                {"id": "pc2", "title": "Arrachera", "description": "$200 kilo"},
            ]}],
        ),
        "tienda2": dict(
            header_text="🛒 Abarrotes",
            body_text="Selecciona el producto que necesitas:",
            sections=[{"title": "🛍️ Te ofrecemos:", "rows": [
                {"id": "ab1", "title": "Arroz",    "description": "$25 kg"},
                {"id": "ab2", "title": "Frijoles", "description": "$30 kg"},
                {"id": "ab3", "title": "Aceite",   "description": "$45 litro"},
            ]}],
        ),
        "tienda3": dict(
            header_text="💊 Farmacia",
            body_text="Selecciona el medicamento que necesitas:",
            sections=[{"title": "🛍️ Te ofrecemos:", "rows": [
                {"id": "fa1", "title": "Paracetamol", "description": "$15 caja"},
                {"id": "fa2", "title": "Ibuprofeno",  "description": "$20 caja"},
            ]}],
        ),
    }
    if tienda_id not in menus:
        sendReply(sender, "Tienda no encontrada. Escribe *hola* para ver el menú. 😊")
        return
    sendListP(to_number=sender, **menus[tienda_id])


# ── Mapa de productos ────────────────────────────────────────
PRODUCTOS = {
    "pc1": ("Bistec",      "carniceria"),
    "pc2": ("Arrachera",   "carniceria"),
    "ab1": ("Arroz",       "abarrotes"),
    "ab2": ("Frijoles",    "abarrotes"),
    "ab3": ("Aceite",      "abarrotes"),
    "fa1": ("Paracetamol", "farmacia"),
    "fa2": ("Ibuprofeno",  "farmacia"),
}

UNIDADES = {
    "carniceria": "kilos",
    "abarrotes":  "unidades",
    "farmacia":   "cajas",
}


# ── Webhook ──────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verificado")
        return challenge, 200
    return "Token inválido", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    print(f"📦 Payload: {data}")

    try:
        entry   = data.get("entry", [])
        changes = entry[0].get("changes", []) if entry else []
        value   = changes[0].get("value", {}) if changes else {}
        messages = value.get("messages", [])

        for msg in messages:
            sender = msg["from"]
            estado = getStatus(sender)
            msg_type = msg.get("type")

            # ── Ubicación recibida ────────────────────────────
            if msg_type == "location":
                lat  = msg["location"]["latitude"]
                lng  = msg["location"]["longitude"]
                print(f"📍 Ubicación de {sender}: {lat}, {lng}")

                if estado.get("esperando_ubicacion"):
                    estado["ubicacion"] = {"lat": lat, "lng": lng}
                    estado["esperando_ubicacion"] = False
                    resumen = summaryBuy(estado["carrito"])
                    sendButtonOptions(
                        to_number=sender,
                        body_text=f"📋 *Resumen de tu pedido:*\n{resumen}\n\n📍 Ubicación recibida. ¿Confirmas el envío?",
                        buttons=[
                            {"id": "pedido_ok",     "title": "✅ Sí, confirmar"},
                            {"id": "pedido_cancel", "title": "❌ Cancelar todo"},
                        ],
                    )
                else:
                    sendReply(sender, "📍 Ubicación recibida. Escribe *hola* para comenzar un pedido.")

            # ── Interactivo (lista o botón) ───────────────────
            elif msg_type == "interactive":
                interactive = msg["interactive"]

                if interactive.get("type") == "button_reply":
                    btn_id = interactive["button_reply"]["id"]

                    if btn_id == "agregar_mas":
                        sendPrincipalMenu(sender)

                    elif btn_id == "confirmar_pedido":
                        finalConfirmation(sender)

                    elif btn_id == "pedido_ok":
                        carrito = estado["carrito"]
                        if carrito:
                            ubicacion = estado.get("ubicacion")
                            providerNotification(carrito, sender)
                            distributorNotification(carrito, sender, ubicacion)
                            sendReply(sender, "✅ ¡Pedido confirmado y enviado! En breve nos ponemos en contacto. 🚚")
                        else:
                            sendReply(sender, "No tienes productos en tu pedido. Escribe *hola* para comenzar.")
                        resetStatus(sender)

                    elif btn_id == "pedido_cancel":
                        resetStatus(sender)
                        sendReply(sender, "❌ Pedido cancelado. Escribe *hola* cuando quieras hacer un nuevo pedido.")

                    else:
                        sendReply(sender, "No reconocí esa opción. Escribe *hola* para ver el menú. 😊")

                elif interactive.get("type") == "list_reply":
                    option_id = interactive["list_reply"]["id"]

                    if option_id in ("tienda1", "tienda2", "tienda3"):
                        sendStoreMenu(sender, option_id)

                    elif option_id in PRODUCTOS:
                        nombre, tienda = PRODUCTOS[option_id]
                        estado["item_en_curso"] = {"producto": nombre, "tienda": tienda}
                        estado["esperando_cantidad"] = True
                        unidad = UNIDADES[tienda]
                        sendReply(sender, f"Anotado ✅ *{nombre}*. ¿Cuántos {unidad} deseas?")

                    else:
                        sendReply(sender, "No reconocí esa opción. Escribe *hola* para ver el menú. 😊")

            # ── Texto ─────────────────────────────────────────
            elif msg_type == "text":
                text = msg["text"]["body"].strip()
                print(f"📩 {sender}: {text}")

                if estado.get("esperando_cantidad") and estado.get("item_en_curso"):
                    item = estado["item_en_curso"]
                    estado["carrito"].append({
                        "tienda":   item["tienda"],
                        "producto": item["producto"],
                        "cantidad": text,
                    })
                    estado["esperando_cantidad"] = False
                    estado["item_en_curso"] = None
                    sendOptionsAgreeConfirm(sender)

                elif any(p in text.lower() for p in ["hola", "menu", "menú", "inicio"]):
                    resetStatus(sender)
                    timestamp = msg.get("timestamp")
                    hora = datetime.fromtimestamp(int(timestamp)).time()
                    if (hora >= datetime.strptime("09:00", "%H:%M").time()) or (hora <= datetime.strptime("01:14", "%H:%M").time()):
                        sendPrincipalMenu(sender)
                    else:
                        sendReply(sender, "No te podemos atender")

                else:
                    sendReply(sender, "Escribe *hola* para ver el menú de opciones. 😊")

            else:
                print(f"⚠️ Tipo no soportado: {msg_type}")

    except Exception as e:
        print(f"❌ Error inesperado: {e} | Data: {data}")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)