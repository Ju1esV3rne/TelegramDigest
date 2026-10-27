
import os
import asyncio
import qrcode
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")


async def main():
    client = TelegramClient(
        "telegram_qr_session",
        api_id,
        api_hash,
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            qr_login = await client.qr_login()

            while True:
                qr = qrcode.QRCode(border=2)
                qr.add_data(qr_login.url)
                qr.make(fit=True)
                qr.print_ascii(invert=True)

                print("\nTelegram → Настройки → Устройства → Подключить устройство")
                print("Ожидаем подтверждения...")

                try:
                    await qr_login.wait()
                    print("Авторизация успешна!")
                    break
                except asyncio.TimeoutError:
                    print("QR-код устарел. Создаём новый...")
                    await qr_login.recreate()
                except SessionPasswordNeededError:
                    import getpass
                    password = getpass.getpass(
                        "Введи облачный пароль Telegram: "
                    )
                    await client.sign_in(password=password)
                    print("Авторизация успешна!")
                    break
        else:
            print("Сессия уже авторизована!")

        group_id = -1001122753791
        dialog = await client.get_entity(group_id)

        print(f"\nПоследние сообщения из группы: {dialog.title}\n")

        async for message in client.iter_messages(dialog, limit=10):
            if message.message:
                print(f"[{message.date:%d.%m.%Y %H:%M}] {message.message}\n")

    except Exception as e:
        print("Ошибка:", type(e).__name__)
        print("Описание:", str(e))

    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
