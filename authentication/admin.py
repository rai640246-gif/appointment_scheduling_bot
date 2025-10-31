from django.contrib import admin
from .models import ChatSession, Service, TimeSlot, AvailableSlot, Appointment


admin.site.register(ChatSession)
admin.site.register(Service)
admin.site.register(TimeSlot)   
admin.site.register(AvailableSlot)
admin.site.register(Appointment)
