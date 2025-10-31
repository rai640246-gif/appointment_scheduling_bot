from django import forms
from django.contrib.auth.models import User
from .models import Service
from django.core.exceptions import ValidationError


class RegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm Password")
    
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'first_name', 'last_name']
    
    def clean_password2(self):
        password = self.cleaned_data.get("password")
        password2 = self.cleaned_data.get("password2")
        
        if password != password2:
            raise ValidationError("password not matched.")
        return password2

class ServiceForm(forms.ModelForm):
    class Meta:
        model= Service
        fields = ['name', 'description', 'active']