from django.shortcuts import render, redirect, get_object_or_404 
from .forms import RegistrationForm, ServiceForm
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login as auth_login, logout
from .models import ChatSession, Service, Appointment, AvailableSlot, TimeSlot
import pytz 
import datetime
import dateparser
from datetime import datetime, timedelta
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse
import openai
import requests
import json
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
from dotenv import load_dotenv 
import os
import re
from django.core.mail import send_mail
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
import re

load_dotenv()

def register(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()
            return redirect('login')
    else:
        form = RegistrationForm()   
    return render(request, 'register.html', {'form': form})

def render_login(request):
    return render(request, 'login.html')            
        
def user_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect('dashboard')
        else: 
            error = "Invalid username or password"
            return render(request, 'login.html', {'error': error})
    return render(request, 'login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
def dashboard(request):
    user = request.user
    if user.is_staff:
        users = User.objects.all()
        chat_sessions = ChatSession.objects.all()
        user_chat = []
        for u in users:
            try:
                chat = ChatSession.objects.get(user=u)
                chat_history = chat.chat_history
            except ChatSession.DoesNotExist:
                chat_history = []
            user_chat.append({
                'username' : u.username,
                'email' : u.email,
                'chat_history': chat_history
             })
        return render(request, 'admin_dashbord.html', {'user_chat':user_chat})
    else:
        try:
            session = ChatSession.objects.get(user=user)
            chat_history = session.chat_history
        except ChatSession.DoesNotExist:
            chat_history = []
        services = Service.objects.filter(active=True)
        return render(request, 'index.html', {'chat_history': chat_history, 'services': services})

def get_available_slots(service, detected_date):
    """
    Return a list of AvailableSlot objects for a given service and date.
    Only return available=True slots.
    Handles timezone normalization properly.
    """
    india_tz = pytz.timezone("Asia/Kolkata")
    
    if detected_date is None:
        detected_date = datetime.now(india_tz)

    # Normalize to start of day in India timezone for comparison
    start_of_day = detected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    # Convert to UTC for database query
    start_of_day_utc = start_of_day.astimezone(pytz.UTC)
    end_of_day_utc = end_of_day.astimezone(pytz.UTC)

    return AvailableSlot.objects.filter(
        time_slot__service=service,
        start_time__range=(start_of_day_utc, end_of_day_utc),
        available=True
    ).order_by('start_time')

def create_appointment(user, service, start_time):
    """Create an appointment and mark the slot as unavailable"""
    try:
        # Calculate end time (assuming 1 hour duration, adjust as needed)
        end_time = start_time + timedelta(hours=1)
        
        # Create appointment
        appointment = Appointment.objects.create(
            user=user,
            service=service,
            start_time=start_time,
            end_time=end_time,
            status='confirmed'
        )
        
        # Mark the time slot as unavailable
        india_tz = pytz.timezone("Asia/Kolkata")
        start_utc = start_time.astimezone(pytz.UTC)
        
        # Find and update the corresponding AvailableSlot
        available_slot = AvailableSlot.objects.filter(
            time_slot__service=service,
            start_time=start_utc,
            available=True
        ).first()
        
        if available_slot:
            available_slot.available = False
            available_slot.save()
        
        return appointment
    except Exception as e:
        print(f"Error creating appointment: {e}")
        return None

@login_required
def chat_history(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request'}, status=400)

    user = request.user
    user_message = request.POST.get('message', '').strip()
    if not user_message:
        return JsonResponse({'reply': "Please type something so I can assist you 😊"})

    india_tz = pytz.timezone("Asia/Kolkata")
    session, _ = ChatSession.objects.get_or_create(user=user)
    chat_log = session.chat_history or []

    # Save user message
    chat_log.append({'sender': 'user', 'text': user_message, 'timestamp': timezone.now().isoformat()})

    # Restore or initialize booking context
    booking_context = request.session.get('booking_context', {
        'intent': None,
        'service': None,
        'detected_date': None,
        'slot_time': None,
        'step': None,
        'initial_message_processed': False
    })
    selected_service_name = request.session.get('selected_service')
    bot_reply = ""

    # -----------------------------------------
    # 🔹 1. Intent detection
    # -----------------------------------------
    user_msg_lower = user_message.lower()
    intents = {
        "book": any(k in user_msg_lower for k in ["book", "appointment", "reserve", "schedule"]),
        "cancel": any(k in user_msg_lower for k in ["cancel", "delete", "remove", "drop"]),
        "reschedule": any(k in user_msg_lower for k in ["reschedule", "move", "shift", "change"]),
        "greet": any(k in user_msg_lower for k in ["hi", "hello", "hey"]),
        "faq": any(k in user_msg_lower for k in ["what", "how", "when", "where", "why", "can i", "do you", "service", "price", "cost"]),
        "check": any(k in user_msg_lower for k in ["check", "view", "see", "my appointment", "appointments"]),
    }

    # Handle greeting
    if intents["greet"] and not (intents["book"] or intents["cancel"] or intents["reschedule"]):
        bot_reply = f"Hi {user.username.capitalize()}! 👋 I'm your virtual salon assistant. How can I help you today — book, reschedule, cancel an appointment, or answer any questions?"
        chat_log.append({'sender': 'bot', 'text': bot_reply, 'timestamp': timezone.now().isoformat()})
        session.chat_history = chat_log
        session.save()
        return JsonResponse({'reply': bot_reply})

    # Handle FAQ questions
    if intents["faq"] and not (intents["book"] or intents["cancel"] or intents["reschedule"]):
        faq_reply = handle_faq_questions(user_msg_lower)
        if faq_reply:
            chat_log.append({'sender': 'bot', 'text': faq_reply, 'timestamp': timezone.now().isoformat()})
            session.chat_history = chat_log
            session.save()
            return JsonResponse({'reply': faq_reply})

    # Handle appointment checking
    if intents["check"]:
        appointments_reply = handle_check_appointments(user)
        chat_log.append({'sender': 'bot', 'text': appointments_reply, 'timestamp': timezone.now().isoformat()})
        session.chat_history = chat_log
        session.save()
        return JsonResponse({'reply': appointments_reply})

    # -----------------------------------------
    # 🔹 2. Enhanced date/time parsing
    # -----------------------------------------
    detected_date = None
    clean_message = user_message.lower()
    for word in ["book", "appointment", "reserve", "schedule", "for", "at", "on", "me", "an", "a"]:
        clean_message = clean_message.replace(word, "")

    clean_message = clean_message.strip()
    print("🧩 Cleaned message for dateparser:", clean_message)

    parsed_datetime = dateparser.parse(
        clean_message,
        settings={
            'TIMEZONE': 'Asia/Kolkata',
            'RETURN_AS_TIMEZONE_AWARE': True,
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': datetime.now(pytz.timezone("Asia/Kolkata")),
        }
    )

    if parsed_datetime:
        detected_date = parsed_datetime.astimezone(india_tz)
        booking_context['detected_date'] = detected_date.isoformat()
    elif booking_context.get('detected_date'):
        detected_date = datetime.fromisoformat(booking_context['detected_date']).astimezone(india_tz)

    # -----------------------------------------
    # 🔹 3. Determine active service
    # -----------------------------------------
    selected_service = None
    active_services = Service.objects.filter(active=True)

    for s in active_services:
        if s.name.lower() in user_msg_lower:
            selected_service = s
            break

    if not selected_service and selected_service_name:
        selected_service = Service.objects.filter(name=selected_service_name).first()

    if selected_service:
        booking_context['service'] = selected_service.id
        request.session['selected_service'] = selected_service.name

    # -----------------------------------------
    # 🔹 4. Handle Cancel intent
    # -----------------------------------------
    if intents["cancel"]:
        cancel_reply, handled = cancel_appointment(request, user, user_msg_lower)
        if handled:
            chat_log.append({'sender': 'bot', 'text': cancel_reply, 'timestamp': timezone.now().isoformat()})
            session.chat_history = chat_log
            session.save()
            return JsonResponse({'reply': cancel_reply})

    # -----------------------------------------
    # 🔹 5. Handle Reschedule intent
    # -----------------------------------------
    if intents["reschedule"]:
        res_msg, handled = handle_reschedule(request, user, user_msg_lower, detected_date, selected_service)
        if handled:
            chat_log.append({'sender': 'bot', 'text': res_msg, 'timestamp': timezone.now().isoformat()})
            session.chat_history = chat_log
            session.save()
            return JsonResponse({'reply': res_msg})

    # -----------------------------------------
    # 🔹 6. ENHANCED Booking flow - Smart detection
    # -----------------------------------------
    if intents["book"] or booking_context.get('intent') == 'book':
        booking_context['intent'] = 'book'
        
        # Check if this is the first message in booking flow with all info
        is_initial_booking_message = (intents["book"] and 
                                    not booking_context.get('initial_message_processed') and
                                    (selected_service or detected_date))
        
        print("this is initial bookink message : ",is_initial_booking_message)

        # If initial message has both service and date, process immediately
        if is_initial_booking_message and selected_service and detected_date:
            booking_context['initial_message_processed'] = True
            available_slots = get_available_slots(selected_service, detected_date)
            print("this is available slots ",available_slots)
            
            # Try to parse time from initial message
            selected_time = parse_time_selection(user_message, available_slots, india_tz)
            print("this is selected time",selected_time)
            
            if selected_time:
                # Direct booking if time is specified
                appointment = create_appointment(user, selected_service, selected_time)
                if appointment:
                    local_time = selected_time.astimezone(india_tz)
                    book_appointment(
                        user.username,
                        user.email,
                        local_time.strftime('%A, %B %d'),
                        local_time.strftime('%I:%M %p'),
                        selected_service.name
                    )
                    bot_reply = (
                        f"✅ Excellent! Your {selected_service.name} appointment is confirmed for "
                        f"{local_time.strftime('%A, %B %d at %I:%M %p')}. "
                        f"You'll receive a confirmation email shortly. 💇‍♀️"
                    )
                    booking_context = {'intent': None, 'service': None, 'detected_date': None, 'slot_time': None, 'step': None, 'initial_message_processed': False}
                    request.session['selected_service'] = None
                else:
                    bot_reply = "Sorry, there was an issue booking your appointment. Please try again."
            else:
                # Show available slots
                if not available_slots.exists():
                    bot_reply = f"Sorry 😕, no slots available for {selected_service.name} on {detected_date.strftime('%A, %B %d')}. Would you like me to check another day?"
                    booking_context['detected_date'] = None
                else:
                    slot_text = ", ".join([s.start_time.astimezone(india_tz).strftime("%I:%M %p") for s in available_slots[:8]])
                    bot_reply = f"Great! I see you want to book {selected_service.name} for {detected_date.strftime('%A, %B %d')}. Available slots: {slot_text}. Which one works for you?"
                    booking_context['step'] = 'time_selection'

        # Standard booking flow steps
        elif not selected_service and not booking_context.get('service'):
            bot_reply = "Got it 👍 What service would you like to book? We offer: " + ", ".join([s.name for s in active_services])
            booking_context['step'] = 'service_selection'
            booking_context['initial_message_processed'] = True

        elif not detected_date and booking_context.get('service'):
            selected_service = Service.objects.get(id=booking_context['service'])
            bot_reply = f"When would you like your {selected_service.name} appointment? (e.g., today 4 pm or tomorrow morning)"
            booking_context['step'] = 'date_selection'

        elif detected_date and booking_context.get('service'):
            selected_service = Service.objects.get(id=booking_context['service'])
            available_slots = get_available_slots(selected_service, detected_date)
            selected_time = parse_time_selection(user_message, available_slots, india_tz)

            if selected_time:
                appointment = create_appointment(user, selected_service, selected_time)
                if appointment:
                    local_time = selected_time.astimezone(india_tz)
                    book_appointment(
                        user.username,
                        user.email,
                        local_time.strftime('%A, %B %d'),
                        local_time.strftime('%I:%M %p'),
                        selected_service.name
                    )
                    bot_reply = (
                        f"✅ Excellent! Your {selected_service.name} appointment is confirmed for "
                        f"{local_time.strftime('%A, %B %d at %I:%M %p')}. "
                        f"You'll receive a confirmation email shortly. 💇‍♀️"
                    )
                    booking_context = {'intent': None, 'service': None, 'detected_date': None, 'slot_time': None, 'step': None, 'initial_message_processed': False}
                    request.session['selected_service'] = None
                else:
                    bot_reply = "Sorry, there was an issue booking your appointment. Please try again."
            else:
                if not available_slots.exists():
                    bot_reply = f"Sorry 😕, no slots available for {selected_service.name} on {detected_date.strftime('%A, %B %d')}. Would you like me to check another day?"
                    booking_context['detected_date'] = None
                else:
                    slot_text = ", ".join([s.start_time.astimezone(india_tz).strftime("%I:%M %p") for s in available_slots[:8]])
                    bot_reply = f"Here are the available slots for {selected_service.name} on {detected_date.strftime('%A, %B %d')}: {slot_text}. Which one would you like?"
                    booking_context['step'] = 'time_selection'

        request.session['booking_context'] = booking_context
        request.session.modified = True
        chat_log.append({'sender': 'bot', 'text': bot_reply, 'timestamp': timezone.now().isoformat()})
        session.chat_history = chat_log
        session.save()
        return JsonResponse({'reply': bot_reply})

    # -----------------------------------------
    # 🔹 7. LLM fallback
    # -----------------------------------------
    try:
        recent_context = "\n".join([f"{msg['sender']}: {msg['text']}" for msg in chat_log[-3:]])
        services_text = "\n".join([f"{s.name}: {s.description}" for s in active_services])
        prompt = (
            f"You are a friendly and smart salon booking assistant.\n"
            f"Available services:\n{services_text}\n\n"
            f"Conversation so far:\n{recent_context}\n\n"
            f"User: {user_message}\nAssistant:"
            f"Keep your responses concise and clear — do not exceed 100 words.\n"
        )
        llm = HuggingFaceEndpoint(
            repo_id="meta-llama/Llama-3.1-8B-Instruct",
            task="text-generation",
            huggingfacehub_api_token=os.getenv("HUGGING_FACE_KEY"),
            temperature=0.6,
            max_new_tokens= 130,
        )
        model = ChatHuggingFace(llm=llm)
        result = model.invoke(prompt)
        bot_reply = getattr(result, "content", str(result)).strip()
    except Exception as e:
        print("❌ AI fallback error:", e)
        bot_reply = "I'm here to help 😊 Would you like to book, reschedule, cancel an appointment, or ask about our services?"

    chat_log.append({'sender': 'bot', 'text': bot_reply, 'timestamp': timezone.now().isoformat()})
    session.chat_history = chat_log
    session.save()
    return JsonResponse({'reply': bot_reply})
def parse_time_selection(user_message, available_slots, timezone):
    """Parse user's time selection from available slots"""
    user_msg_lower = user_message.lower().strip()
    
    # Try to match exact time formats
    for slot in available_slots:
        slot_time_str = slot.start_time.astimezone(timezone).strftime("%I:%M %p").lower()
        slot_time_str_alt = slot.start_time.astimezone(timezone).strftime("%I %p").lower().replace(" 0", " ")
        
        # Remove leading zeros for single-digit hours
        slot_time_clean = slot_time_str.replace(" 0", " ")
        
        if (slot_time_str in user_msg_lower or 
            slot_time_str_alt in user_msg_lower or
            slot_time_clean in user_msg_lower or
            f"at {slot_time_str}" in user_msg_lower or
            slot.start_time.astimezone(timezone).strftime("%H:%M") in user_msg_lower):
            return slot.start_time
    
    # Try to parse time using dateparser as fallback
    parsed_time = dateparser.parse(
        user_message,
        settings={
            'TIMEZONE': 'Asia/Kolkata',
            'RETURN_AS_TIMEZONE_AWARE': True,
        }
    )
    
    if parsed_time:
        # Find the closest matching slot
        for slot in available_slots:
            slot_time = slot.start_time.astimezone(timezone)
            time_diff = abs((slot_time - parsed_time).total_seconds())
            if time_diff <= 30 * 60:  # 30 minutes tolerance
                return slot.start_time
    
    return None
def handle_faq_questions(user_message):
    """Handle frequently asked questions"""
    user_msg_lower = user_message.lower()
    
    if any(word in user_msg_lower for word in ["service", "offer", "provide"]):
        services = Service.objects.filter(active=True)
        service_list = ", ".join([s.name for s in services])
        return f"We offer the following services: {service_list}. Which one are you interested in?"
    
    elif any(word in user_msg_lower for word in ["price", "cost", "how much"]):
        return "Our prices vary by service. Please let me know which service you're interested in, and I'll provide the specific pricing!"
    
    elif any(word in user_msg_lower for word in ["time", "hour", "open"]):
        return "We're open Monday to Saturday from 9:00 AM to 8:00 PM, and Sunday from 10:00 AM to 6:00 PM."
    
    elif any(word in user_msg_lower for word in ["location", "where", "address"]):
        return "We're located at 123 Beauty Street, Salon District. Come visit us!"
    
    elif any(word in user_msg_lower for word in ["cancel", "cancellation"]):
        return "You can cancel your appointment anytime through our chat. Just let me know you want to cancel!"
    
    elif any(word in user_msg_lower for word in ["reschedule", "change"]):
        return "To reschedule, just tell me you want to change your appointment and provide the new date and time."
    
    return None

def handle_check_appointments(user):
    """Check user's upcoming appointments"""
    india_tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(india_tz)
    
    upcoming_appointments = Appointment.objects.filter(
        user=user,
        start_time__gte=now
    ).order_by('start_time')
    
    if not upcoming_appointments.exists():
        return "You don't have any upcoming appointments. Would you like to book one?"
    
    appointments_list = []
    for appointment in upcoming_appointments:
        local_time = appointment.start_time.astimezone(india_tz)
        appointments_list.append(
            f"{appointment.service.name} on {local_time.strftime('%A, %B %d at %I:%M %p')}"
        )
    
    if len(appointments_list) == 1:
        return f"You have an upcoming appointment: {appointments_list[0]}"
    else:
        return f"You have {len(appointments_list)} upcoming appointments: " + "; ".join(appointments_list)

def service_list(request):
    services = Service.objects.all()
    return render(request, 'service_list.html', {'services': services})

def service_create(request):
    if request.method == 'POST':
        form = ServiceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('service_list')
    else:
        form = ServiceForm()
        return render(request, 'service_form.html', {'form':form, 'title': 'Add Service'})

def service_update(request,pk):
    service = get_object_or_404(Service, pk=pk)
    if request.method == 'POST':
        form = ServiceForm(request.POST, instance=service)
        if form.is_valid():
            form.save()
            return redirect('service_list')
    else:
        form = ServiceForm(instance=service)
    return render(request, 'service_form.html', {'form': form, 'title': 'Edit Service'})

def service_delete(request,pk):
    service = get_object_or_404(Service, pk=pk)
    if request.method == 'POST':
        service.delete()
        return redirect('service_list')
    return render(request, 'service_confirm_delete.html', {'service': service})

@login_required
def select_service(request):
    if request.method == 'POST':
        service = request.POST.get('service')
        request.session['selected_service'] = service
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Invalid request'}, status=400)

def book_appointment(name, email, date, time, service):
    """Send appointment confirmation email"""
    subject = f"Appointment Confirmation - {service}"
    message = (
        f"Hello {name},\n\n"
        f"Your appointment for {service} has been successfully booked.\n"
        f"📅 Date: {date}\n"
        f"🕒 Time: {time}\n\n"
        f"We look forward to seeing you!\n\n"
        f"— The Salon Team"
    )

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False

def get_appointments(request):
    appointments = Appointment.objects.select_related('service', 'user').all()
    data = []
    for a in appointments:
        local_time = timezone.localtime(a.start_time) if a.start_time else None
        data.append({
            "id": a.id,
            "email": a.user.email,
            "user": a.user.username,
            "service": a.service.name if a.service else None,
            "start_time": local_time.strftime('%Y-%m-%d %I:%M %p') if local_time else None,
            "status": a.status,
            "created_at": timezone.localtime(a.created_at).strftime('%Y-%m-%d %I:%M %p')   
        })
    return render(request, "appointment_list.html", {"appointments": data})

def handle_reschedule(request, user, user_message_lower, detected_date, selected_service):
    """Handle appointment rescheduling"""
    india_tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(india_tz)

    # Get upcoming appointments
    upcoming_appointments = Appointment.objects.filter(
        user=user,
        start_time__gte=now
    ).order_by('start_time')

    if not upcoming_appointments.exists():
        return "You don't have any upcoming appointments to reschedule. Would you like to book one instead?", True

    latest_appointment = upcoming_appointments.first()

    reschedule_keywords = ["reschedule", "change my appointment", "move", "update time", "shift", "modify"]

    if any(word in user_message_lower for word in reschedule_keywords):
        if detected_date and selected_service:
            available_slots = get_available_slots(selected_service, detected_date)
            if not available_slots.exists():
                return f"Sorry 😕, no available slots for {selected_service.name} around that time.", True

            matched_slot = None
            for slot in available_slots:
                slot_start = slot.start_time.astimezone(india_tz)
                if abs((slot_start - detected_date).total_seconds()) <= 30 * 60:
                    matched_slot = slot
                    break

            if matched_slot:
                latest_appointment.start_time = matched_slot.start_time
                latest_appointment.end_time = matched_slot.end_time
                latest_appointment.save()
                matched_slot.available = False
                matched_slot.save()
                slot_local = matched_slot.start_time.astimezone(india_tz)
                return f"✅ Your {selected_service.name} appointment has been rescheduled to {slot_local.strftime('%I:%M %p on %A, %B %d')}!", True
            else:
                return f"Sorry 😕, no nearby slots available around {detected_date.strftime('%I:%M %p on %A, %B %d')}.", True

        service_name = latest_appointment.service.name if latest_appointment.service else "your service"
        appointment_time = latest_appointment.start_time.astimezone(india_tz)
        return (
            f"Sure, you currently have an appointment for {service_name} "
            f"on {appointment_time.strftime('%I:%M %p, %A %B %d')}. "
            f"When would you like to reschedule it to?",
            True
        )

    return None, False


def cancel_appointment(request, user, user_message_lower):
    """Handle appointment cancellation"""
    cancel_keywords = [
        "cancel", "delete", "remove", "call off", "drop",
        "cancel my appointment", "cancel appointment"
    ]

    if not any(word in user_message_lower for word in cancel_keywords):
        return None, False

    india_tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(india_tz)

    # Get upcoming appointments
    upcoming_appointments = Appointment.objects.filter(
        user=user,
        start_time__gte=now
    ).order_by('start_time')

    if not upcoming_appointments.exists():
        return "You don't have any upcoming appointments to cancel.", True

    # Pick earliest appointment to cancel
    appointment_to_cancel = upcoming_appointments.first()
    cancelled_service = appointment_to_cancel.service.name if appointment_to_cancel.service else "your service"
    cancelled_time = appointment_to_cancel.start_time.astimezone(india_tz)
    formatted_date = cancelled_time.strftime("%I:%M %p on %A, %B %d")

    # --- FIX: Free up the time slot safely ---
    start_utc = appointment_to_cancel.start_time.astimezone(pytz.UTC)
    end_utc = appointment_to_cancel.end_time.astimezone(pytz.UTC)

    # Use tolerance for microsecond differences
    freed_slots = AvailableSlot.objects.filter(
        time_slot__service=appointment_to_cancel.service,
        start_time__range=(start_utc - timedelta(minutes=1), start_utc + timedelta(minutes=1))
    )

    if freed_slots.exists():
        for slot in freed_slots:
            slot.available = True
            slot.save()
    else:
        print("⚠️ No matching slot found to free up!")

    # Delete appointment
    appointment_to_cancel.delete()

    # Clear session context
    request.session['booking_context'] = {}
    request.session['selected_service'] = None
    request.session.modified = True

    # Send cancellation email
    subject = "Your Salon Appointment Has Been Cancelled"
    message = (
        f"Hi {user.username},\n\n"
        f"Your {cancelled_service} appointment scheduled for {formatted_date} "
        f"has been successfully cancelled.\n\n"
        "We're sorry to see you cancel, but we hope to welcome you again soon!\n\n"
        "Warm regards,\nThe Salon Team 💇‍♀️"
    )

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception as e:
        print(f"Email sending failed: {e}")

    return (
        f"Your {cancelled_service} appointment for {formatted_date} has been successfully cancelled. "
        f"The slot is now available for booking again. ✅",
        True
    )
