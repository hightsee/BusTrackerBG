import logging
import threading
from telegram.ext import ApplicationBuilder, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler

from config import BOT_TOKEN
from db_manager import bot_data_manager
from gtfs_manager import gtfs_manager
import handlers

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

scheduler = BackgroundScheduler()

def scheduled_gtfs_update():
    logging.info("Running scheduled GTFS update...")
    gtfs_manager.update_gtfs()

if not gtfs_manager.get_last_update():
    logging.info("First run: Starting GTFS database build in background...")
    threading.Thread(target=gtfs_manager.update_gtfs).start()

scheduler.add_job(scheduled_gtfs_update, 'cron', day_of_week='mon', hour=4)
scheduler.start()

bot_data_manager.migrate_from_json()
bot_data_manager.fix_missing_station_ids()

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('start', handlers.start))
    application.add_handler(CommandHandler('stations', handlers.stations))
    application.add_handler(CommandHandler('search', handlers.search))
    application.add_handler(CommandHandler('check', handlers.check))
    application.add_handler(CommandHandler('save', handlers.save_favorite))
    application.add_handler(CommandHandler('favorites', handlers.list_favorites))
    application.add_handler(CommandHandler('delete', handlers.delete_favorite))
    application.add_handler(CommandHandler('help', handlers.help_command))
    application.add_handler(CommandHandler('users', handlers.users_command))
    
    # GTFS Handlers
    application.add_handler(CommandHandler('timetable', handlers.timetable))
    application.add_handler(CommandHandler('predict', handlers.predict_command))
    application.add_handler(CommandHandler('nextat', handlers.nextat_command))
    application.add_handler(CommandHandler('route', handlers.route_command))
    application.add_handler(CommandHandler('refreshtimetable', handlers.refresh_timetable))
    application.add_handler(CommandHandler('timetablestatus', handlers.timetable_status))
    
    print("Bot is running...")
    application.run_polling()
