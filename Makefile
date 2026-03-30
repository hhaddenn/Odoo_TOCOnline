.PHONY: help up down build migrate makemigrations shell logs test health-sync

help:
	@echo "Comandos disponíveis:"
	@echo "  make up              - Sobe todos os serviços (docker compose up)"
	@echo "  make down            - Para todos os serviços"
	@echo "  make build           - Reconstrói a imagem Docker"
	@echo "  make migrate         - Corre migrações Django"
	@echo "  make makemigrations  - Cria novas migrações"
	@echo "  make shell           - Django shell interactivo"
	@echo "  make superuser       - Cria superuser Django"
	@echo "  make logs            - Mostra logs de todos os serviços"
	@echo "  make test            - Corre testes"
	@echo "  make health-sync     - Diagnóstico rápido de sync/health automático"

up:
	docker compose up

down:
	docker compose down

build:
	docker compose build --no-cache

migrate:
	docker compose run --rm web python manage.py migrate

makemigrations:
	docker compose run --rm web python manage.py makemigrations

shell:
	docker compose run --rm web python manage.py shell

superuser:
	docker compose run --rm web python manage.py createsuperuser

logs:
	docker compose logs -f

test:
	docker compose run --rm web python manage.py test

health-sync:
	docker compose exec -T web python manage.py shell -c "from audit.models import SyncLog; from django.utils import timezone; from datetime import timedelta; from django_celery_beat.models import PeriodicTask; import json; now=timezone.now(); since=now-timedelta(minutes=30); data={'periodic_tasks': list(PeriodicTask.objects.filter(name__startswith='auto_').values('name','enabled','interval__every','interval__period')), 'logs_last_30m': SyncLog.objects.filter(created_at__gte=since).count(), 'errors_last_30m': SyncLog.objects.filter(created_at__gte=since,status='error').count(), 'last_log': SyncLog.objects.order_by('-created_at').values('created_at','entity_type','status','error_message').first()}; print(json.dumps(data, default=str, ensure_ascii=False, indent=2))"
