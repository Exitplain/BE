from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('runs', '0003_observation_stage_session_stages'),
    ]

    operations = [
        migrations.AddField(
            model_name='observation',
            name='title',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
