import os
import logging
from telegram import Update, InputFile
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from zipfile import ZipFile
import tempfile
from datetime import datetime
import math
import subprocess  # Para soporte RAR
import shutil  # Para manejo de archivos

# Configuración
TOKEN = "8158988158:AAHejJ1AdlSSjRfF4XMTOA4fmX9VoXonAgk"
INACTIVITY_TIME = 10  # Segundos para compresión automática
MAX_ZIP_SIZE = 1.95 * 1024 * 1024 * 1024  # 1.95GB
MAX_FILE_SIZE = 1.8 * 1024 * 1024 * 1024  # 1.8GB por archivo
AUTHORIZED_USER_ID = 5140106953  # Tu ID
RAR_PATH = "/usr/bin/rar"  # Ruta al ejecutable RAR (instalar previamente)

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    filename='compressor_bot.log'
)
logger = logging.getLogger(__name__)

class UserSession:
    def __init__(self):
        self.files = []
        self.last_activity = datetime.now()
        self.compress_job = None
        self.current_total_size = 0
        self.compression_format = 'zip'  # Por defecto ZIP

user_data = {}

def format_size(size_bytes):
    """Formatea bytes a formato legible"""
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def restricted(func):
    """Decorador para restringir acceso"""
    def wrapped(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != AUTHORIZED_USER_ID:
            update.message.reply_text("🔒 Bot privado - Acceso denegado")
            logger.warning(f"Intento de acceso no autorizado desde ID: {user_id}")
            return
        return func(update, context, *args, **kwargs)
    return wrapped

@restricted
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    user_data[user_id] = UserSession()
    
    help_text = (
        "🔐 *Bot de Compresión Avanzado*\n\n"
        "📦 Formatos soportados: ZIP, RAR\n"
        "⚙️ Límite: 1.95GB por archivo comprimido\n\n"
        "🔧 *Comandos disponibles:*\n"
        "/start - Muestra este mensaje\n"
        "/zip - Crea comprimido en formato ZIP\n"
        "/rar - Crea comprimido en formato RAR\n"
        "/cancel - Cancela la operación actual\n\n"
        "📤 Envíame archivos y luego usa un comando de formato\n"
        "⏳ O espera 10s para compresión automática (ZIP)"
    )
    
    update.message.reply_text(help_text, parse_mode='Markdown')

@restricted
def set_zip_format(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id in user_data:
        user_data[user_id].compression_format = 'zip'
        update.message.reply_text("🔄 Formato configurado: ZIP")
        compress_files(user_id, context, automatic=False)

@restricted
def set_rar_format(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id in user_data:
        # Verificar si RAR está instalado
        if not os.path.exists(RAR_PATH):
            update.message.reply_text(
                "❌ RAR no está instalado en el servidor\n"
                "Usando ZIP por defecto"
            )
            user_data[user_id].compression_format = 'zip'
            return
        
        user_data[user_id].compression_format = 'rar'
        update.message.reply_text("🔄 Formato configurado: RAR")
        compress_files(user_id, context, automatic=False)

@restricted
def handle_file(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    
    if user_id not in user_data:
        user_data[user_id] = UserSession()
    
    session = user_data[user_id]
    
    try:
        file = update.message.effective_attachment
        file_name = getattr(file, 'file_name', f'file_{len(session.files)+1}')
        file_size = file.file_size
        
        # Verificar tamaño
        if file_size > MAX_FILE_SIZE:
            update.message.reply_text(
                f"⚠️ Archivo demasiado grande:\n"
                f"{format_size(file_size)} > {format_size(MAX_FILE_SIZE)}"
            )
            return
        
        if session.current_total_size + file_size > MAX_ZIP_SIZE:
            update.message.reply_text(
                f"⚠️ Límite total casi alcanzado:\n"
                f"Actual: {format_size(session.current_total_size)}\n"
                f"Usa /zip o /rar para comprimir ahora"
            )
            return
        
        # Añadir archivo
        session.files.append({
            'file_id': file.file_id,
            'file_name': file_name,
            'file_obj': context.bot.get_file(file.file_id),
            'size': file_size
        })
        session.current_total_size += file_size
        session.last_activity = datetime.now()
        
        # Reprogramar temporizador (solo para ZIP automático)
        if session.compress_job:
            context.job_queue.jobs()[session.compress_job].schedule_removal()
        
        session.compress_job = f"compress_{user_id}"
        context.job_queue.run_once(
            auto_compress,
            when=INACTIVITY_TIME,
            context=user_id,
            name=session.compress_job
        )
        
        update.message.reply_text(
            f"📥 {file_name} recibido\n"
            f"📊 Total: {len(session.files)} archivos\n"
            f"📦 Tamaño acumulado: {format_size(session.current_total_size)}\n"
            f"🔄 Formato actual: {session.compression_format.upper()}"
        )
        
    except Exception as e:
        logger.error(f"Error al procesar archivo: {str(e)}")
        update.message.reply_text("❌ Error al procesar el archivo")

def create_zip(session, user_id, context):
    """Crea archivo ZIP"""
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
        zip_path = tmp_file.name
    
    try:
        with ZipFile(zip_path, 'w') as zipf:
            for file_info in session.files:
                file_path = f"/tmp/{file_info['file_name']}"
                file_info['file_obj'].download(file_path)
                zipf.write(file_path, file_info['file_name'])
                os.remove(file_path)
        
        return zip_path
    except Exception as e:
        logger.error(f"Error al crear ZIP: {str(e)}")
        raise

def create_rar(session, user_id, context):
    """Crea archivo RAR usando el binario RAR"""
    if not os.path.exists(RAR_PATH):
        raise Exception("RAR no está instalado en el servidor")
    
    temp_dir = tempfile.mkdtemp()
    rar_path = os.path.join(temp_dir, "archivos.rar")
    
    try:
        # Descargar todos los archivos primero
        file_paths = []
        for file_info in session.files:
            file_path = os.path.join(temp_dir, file_info['file_name'])
            file_info['file_obj'].download(file_path)
            file_paths.append(file_path)
        
        # Comando RAR (necesita tener permisos ejecutables)
        cmd = [RAR_PATH, 'a', '-r', rar_path] + file_paths
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"Error RAR: {result.stderr}")
        
        return rar_path
    except Exception as e:
        logger.error(f"Error al crear RAR: {str(e)}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        # Limpiar archivos temporales (excepto el RAR)
        for file_path in file_paths:
            if os.path.exists(file_path):
                os.remove(file_path)

def compress_files(user_id: int, context: CallbackContext, automatic: bool) -> None:
    if user_id not in user_data or not user_data[user_id].files:
        context.bot.send_message(user_id, "ℹ️ No hay archivos para comprimir")
        return
    
    session = user_data[user_id]
    
    # Limpiar temporizador si existe
    if session.compress_job:
        try:
            context.job_queue.jobs()[session.compress_job].schedule_removal()
        except:
            pass
    
    try:
        # Seleccionar formato
        if automatic:  # La compresión automática siempre usa ZIP
            format_type = 'zip'
            file_ext = 'zip'
        else:
            format_type = session.compression_format
            file_ext = session.compression_format
        
        # Crear archivo comprimido
        if format_type == 'zip':
            compressed_path = create_zip(session, user_id, context)
        elif format_type == 'rar':
            compressed_path = create_rar(session, user_id, context)
        else:
            raise ValueError(f"Formato no soportado: {format_type}")
        
        # Verificar tamaño
        compressed_size = os.path.getsize(compressed_path)
        if compressed_size > MAX_ZIP_SIZE:
            raise ValueError(
                f"Archivo comprimido demasiado grande:\n"
                f"{format_size(compressed_size)} > {format_size(MAX_ZIP_SIZE)}"
            )
        
        # Enviar archivo
        with open(compressed_path, 'rb') as compressed_file:
            context.bot.send_document(
                chat_id=user_id,
                document=InputFile(
                    compressed_file, 
                    filename=f'archivos_comprimidos.{file_ext}'
                ),
                caption=(
                    f"📦 {'Auto-compresión' if automatic else 'Compresión manual'} ({file_ext.upper()})\n"
                    f"• Archivos: {len(session.files)}\n"
                    f"• Tamaño: {format_size(compressed_size)}"
                ),
                timeout=300
            )
        
        # Resetear sesión
        session.files = []
        session.current_total_size = 0
        session.compress_job = None
        
    except Exception as e:
        logger.error(f"Error en compresión: {str(e)}")
        error_msg = f"❌ Error: {str(e)}"
        if "RAR no está instalado" in str(e):
            error_msg += "\n🔧 Usando ZIP por defecto"
            session.compression_format = 'zip'
            compress_files(user_id, context, automatic)
            return
        
        context.bot.send_message(user_id, error_msg)
    finally:
        if 'compressed_path' in locals() and os.path.exists(compressed_path):
            if format_type == 'rar':
                shutil.rmtree(os.path.dirname(compressed_path), ignore_errors=True)
            else:
                os.remove(compressed_path)

def auto_compress(context: CallbackContext) -> None:
    user_id = context.job.context
    if user_id in user_data:
        compress_files(user_id, context, automatic=True)

@restricted
def cancel(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id in user_data:
        session = user_data[user_id]
        if session.compress_job:
            context.job_queue.jobs()[session.compress_job].schedule_removal()
        
        count = len(session.files)
        total_size = session.current_total_size
        session.files = []
        session.current_total_size = 0
        session.compress_job = None
        
        update.message.reply_text(
            f"♻️ Operación cancelada\n"
            f"🗑️ {count} archivos eliminados\n"
            f"📦 Espacio liberado: {format_size(total_size)}"
        )
    else:
        update.message.reply_text("ℹ️ No hay operación activa")

def main() -> None:
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher

    # Comandos
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("zip", set_zip_format))
    dispatcher.add_handler(CommandHandler("rar", set_rar_format))
    dispatcher.add_handler(CommandHandler("cancel", cancel))
    
    # Manejador de archivos
    dispatcher.add_handler(MessageHandler(
        Filters.document | Filters.photo | Filters.audio | Filters.video,
        handle_file
    ))

    # Iniciar bot
    updater.start_polling()
    logger.info(f"🤖 Bot iniciado - Usuario autorizado: {AUTHORIZED_USER_ID}")
    print("Bot en ejecución. Presiona Ctrl+C para detener.")
    updater.idle()

if __name__ == '__main__':
    main()