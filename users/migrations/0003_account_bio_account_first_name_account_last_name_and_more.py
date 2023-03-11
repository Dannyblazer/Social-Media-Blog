# Generated by Django 4.1.3 on 2023-02-26 15:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_account_hide_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='bio',
            field=models.CharField(max_length=250, null=True),
        ),
        migrations.AddField(
            model_name='account',
            name='first_name',
            field=models.CharField(max_length=30, null=True),
        ),
        migrations.AddField(
            model_name='account',
            name='last_name',
            field=models.CharField(max_length=30, null=True),
        ),
        migrations.AddField(
            model_name='account',
            name='role',
            field=models.CharField(max_length=30, null=True),
        ),
        migrations.DeleteModel(
            name='Profile',
        ),
    ]
