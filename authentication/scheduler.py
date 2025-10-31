from apscheduler.schedulers.background import BackgroundScheduler
from django.utils import timezone
from .models import Appointment
from django.core.mail import send_mail
from django.conf import settings
from datetime import timedelta
import pytz

def send_appointment_reminders():
    now = timezone.now()
    appointments = Appointment.objects.select_related('user', 'service').all()

    india_tz = pytz.timezone('Asia/Kolkata')
    utc = pytz.UTC

    for appt in appointments:
        if not appt.detected_date:
            continue

        reminder_sent = getattr(appt, 'reminder_sent', False)
        appt_time = appt.detected_date

        # ✅ STEP 1: Force interpret naive datetime as UTC (SQLite stores as naive UTC)
        if timezone.is_naive(appt_time):
            appt_time = appt_time.replace(tzinfo=utc)

        # ✅ STEP 2: Convert UTC → IST for both comparison & email display
        appt_time_ist = appt_time.astimezone(india_tz)
        time_diff = appt_time - now

        # Skip past appointments
        if appt_time <= now:
            continue

        # CASE 1: Appointment < 24 hours away → send ~1 hour before (for testing: 2 min before)
        if time_diff <= timedelta(hours=24):
            send_time = appt_time - timedelta(minutes=2)
            if send_time <= now < appt_time and not reminder_sent:
                formatted_time = appt_time_ist.strftime('%I:%M %p on %A, %B %d')

                send_mail(
                    subject=f"Reminder: Your appointment for {appt.service.name}",
                    message=(
                        f"Hello {appt.user.username},\n\n"
                        f"This is a friendly reminder that your {appt.service.name} appointment "
                        f"is scheduled for {formatted_time} (India Time).\n\n"
                        f"See you soon!\n\n— The Team"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[appt.user.email],
                    fail_silently=False,  # Change to True in prod
                )
                appt.reminder_sent = True
                appt.save()

        # CASE 2: Appointment > 24 hours away → send 24 hours before
        elif timedelta(hours=23, minutes=59) < time_diff <= timedelta(hours=25):
            send_time = appt_time - timedelta(hours=24)
            if send_time <= now < send_time + timedelta(minutes=10) and not reminder_sent:
                formatted_time = appt_time_ist.strftime('%A, %B %d at %I:%M %p')

                send_mail(
                    subject=f"Reminder: Your upcoming {appt.service.name} appointment",
                    message=(
                        f"Hello {appt.user.username},\n\n"
                        f"This is a reminder that your appointment for {appt.service.name} "
                        f"is scheduled on {formatted_time} (India Time).\n\n"
                        f"We look forward to serving you!\n\n— The Team"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[appt.user.email],
                    fail_silently=False,
                )
                appt.reminder_sent = True
                appt.save()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=timezone.get_current_timezone())
    scheduler.add_job(send_appointment_reminders, 'interval', minutes=10)
    scheduler.start()
