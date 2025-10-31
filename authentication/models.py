from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

class ChatSession(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    chat_history = models.JSONField(default=list) 
    last_updated = models.DateTimeField(auto_now=True)
    
    def is_expired(self):
        return timezone.now() - self.last_updated > timedelta(hours=24)
    
    def __str__(self):
        return f"ChatSession for {self.user.username}"

class Service(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank= True)
    active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name

class Appointment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    detected_date = models.DateTimeField(null=True, blank=True)
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
    original_text = models.TextField()
    reminder_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50, default="confirmed")
    def __str__(self):
        local_time = timezone.localtime(self.detected_date) if self.detected_date else "N/A"
        service_name = self.service.name if self.service else "N/A"
        return f"Appointment for {self.user.username} - {service_name} on {local_time}"



class TimeSlot(models.Model):
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    available = models.BooleanField(default=True)
    
    def save(self, *args, **kwargs):
        is_new = self.pk is None  # Check if this is a new object
        super().save(*args, **kwargs)  # Save the main TimeSlot first
        
        if is_new:
            self.create_available_slots()
    
    def create_available_slots(self):
        """Create 2-hour available slots between start_time and end_time"""
        current_time = self.start_time
        two_hours = timedelta(hours=2)
        
        while current_time + two_hours <= self.end_time:
            slot_end = current_time + two_hours
            
            # Create available slot
            AvailableSlot.objects.create(
                time_slot=self,
                start_time=current_time,
                end_time=slot_end,
                available=True
            )
            
            current_time = slot_end  # Move to next slot
    
    def __str__(self):
        return f"{self.service.name} from {self.start_time} to {self.end_time} - {'Available' if self.available else 'Booked'}"

class AvailableSlot(models.Model):
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name='available_slots')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Slot from {self.start_time} to {self.end_time} - {'Available' if self.available else 'Booked'}"