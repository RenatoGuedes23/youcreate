#!/usr/bin/env bash
# Setup inicial de uma EC2 Ubuntu nova para rodar o youcreate via Docker
# Compose. Roda uma vez, manualmente, antes do primeiro deploy pelo
# GitHub Actions (.github/workflows/deploy.yml).
set -e

echo "== Atualizando pacotes =="
sudo apt-get update -y

echo "== Instalando Docker Engine + Compose plugin (script oficial da Docker) =="
curl -fsSL https://get.docker.com | sudo sh

echo "== Adicionando $USER ao grupo docker (pra rodar docker sem sudo) =="
sudo usermod -aG docker "$USER"

echo "== Garantindo git instalado =="
sudo apt-get install -y git

echo ""
echo "== Verificacao =="
docker --version
docker compose version
git --version

echo ""
echo "Tudo instalado. IMPORTANTE: o grupo 'docker' so tem efeito numa sessao"
echo "SSH nova -- rode 'exit' e conecte de novo (ou rode 'newgrp docker' nesta"
echo "mesma sessao) antes de tentar 'docker compose up' manualmente."
