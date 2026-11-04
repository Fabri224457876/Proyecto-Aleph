client_max_body_size 64m;
add_header X-Content-Type-Options nosniff always;
add_header Referrer-Policy no-referrer always;
add_header X-Frame-Options DENY always;

location /api/ {
    proxy_pass http://api:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 600s;   # FUNES y TLÖN pueden tardar con modelos locales
}
location = /health { proxy_pass http://api:8000; }
location / { try_files $uri /index.html; }
