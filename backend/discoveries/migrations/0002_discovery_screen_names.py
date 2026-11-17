from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discoveries', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='discovery',
            name='screen_names',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
