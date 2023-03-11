# Generated by Django 4.1.3 on 2023-02-26 15:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_account_bio_account_first_name_account_last_name_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='account',
            name='bio',
            field=models.CharField(blank=True, max_length=250, null=True),
        ),
        migrations.AlterField(
            model_name='account',
            name='first_name',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AlterField(
            model_name='account',
            name='last_name',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AlterField(
            model_name='account',
            name='role',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
    ]
