FROM golang:1.24-alpine AS build
ENV GOTOOLCHAIN=local
RUN go install github.com/mailhog/MailHog@v1.0.1

FROM alpine:3.20
RUN apk add --no-cache ca-certificates dos2unix wget
COPY --from=build /go/bin/MailHog /usr/local/bin/mailhog

COPY commands/setup_mailhog_auth.sh /usr/local/bin/setup_mailhog_auth.sh
RUN dos2unix /usr/local/bin/setup_mailhog_auth.sh && chmod +x /usr/local/bin/setup_mailhog_auth.sh

EXPOSE 1025 8025
ENTRYPOINT ["/bin/sh","-lc","/usr/local/bin/setup_mailhog_auth.sh"]
