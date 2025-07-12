# Name of your image and tag (override from CLI)
IMAGE ?= mcpo
TAG ?= latest

.PHONY: docker-build docker-push docker-tag

docker-build:
	docker build -t $(IMAGE):$(TAG) .

docker-push:
	docker push $(IMAGE):$(TAG)
