#!/bin/bash
source venv/bin/activate
pm2 start ecosystem.config.js
pm2 save
echo "LeoScribeBot started with PM2!"
