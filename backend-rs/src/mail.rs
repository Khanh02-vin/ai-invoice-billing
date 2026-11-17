//! Gửi email SMTP dùng chung — port src/mail.py với lettre.
//!
//! Chưa cấu hình SMTP (EMAIL_SMTP_HOST/PORT) -> send_mail trả false; caller
//! quyết định (503 fail-closed ở auth, im lặng bỏ qua ở notify).
//!
//! Khớp hành vi Python: port 465 → implicit TLS (SMTP_SSL); còn lại → STARTTLS
//! bắt buộc. Không thiết lập được TLS → false (fail-closed), không gửi plain.

use lettre::message::header::ContentType;
use lettre::message::{Mailbox, Message};
use lettre::transport::smtp::authentication::Credentials;
use lettre::transport::smtp::AsyncSmtpTransportBuilder;
use lettre::{AsyncSmtpTransport, AsyncTransport, Tokio1Executor};
use std::time::Duration;

use crate::settings::Settings;

/// Địa chỉ nhận: cột email, fallback username nếu username chính là email.
pub fn user_email(email: &str, username: &str) -> String {
    if !email.is_empty() {
        return email.to_owned();
    }
    if username.contains('@') {
        username.to_owned()
    } else {
        String::new()
    }
}

async fn try_send(
    builder: AsyncSmtpTransportBuilder,
    port: u16,
    creds: Option<Credentials>,
    message: Message,
) -> Result<(), String> {
    let mut builder = builder.port(port).timeout(Some(Duration::from_secs(15)));
    if let Some(c) = creds {
        builder = builder.credentials(c);
    }
    builder
        .build()
        .send(message)
        .await
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Gửi 1 email text. True nếu SMTP đã nhận (best-effort, lỗi được log lại).
pub async fn send_mail(settings: &Settings, to: &str, subject: &str, body: &str) -> bool {
    if !settings.email_enabled() || to.is_empty() {
        return false;
    }
    let from = if settings.email_from.is_empty() {
        settings.smtp_user.clone()
    } else {
        settings.email_from.clone()
    };
    let (Ok(from_mailbox), Ok(to_mailbox)) = (from.parse::<Mailbox>(), to.parse::<Mailbox>())
    else {
        return false;
    };
    let make_message = || {
        Message::builder()
            .from(from_mailbox.clone())
            .to(to_mailbox.clone())
            .subject(subject)
            .header(ContentType::TEXT_PLAIN)
            .body(body.to_owned())
            .ok()
    };
    let creds = if settings.smtp_user.is_empty() {
        None
    } else {
        Some(Credentials::new(
            settings.smtp_user.clone(),
            settings.smtp_pass.clone(),
        ))
    };
    let host = settings.smtp_host.clone();

    if settings.smtp_port == 465 {
        // smtplib.SMTP_SSL — implicit TLS, lỗi → False (không fallback plain)
        return match AsyncSmtpTransport::<Tokio1Executor>::relay(&host) {
            Ok(builder) => match try_send(builder, settings.smtp_port, creds, make_message().expect("message đã kiểm tra mailbox")).await {
                Ok(()) => true,
                Err(e) => {
                    eprintln!("[quanh-ta] Gửi email thất bại -> {to}: {e}");
                    false
                }
            },
            Err(e) => {
                eprintln!("[quanh-ta] Gửi email thất bại -> {to}: {e}");
                false
            }
        };
    }

    // SMTP + STARTTLS bắt buộc: email chứa link xác minh/reset — không bao giờ
    // gửi plain. Không thiết lập được STARTTLS → false (fail-closed).
    match AsyncSmtpTransport::<Tokio1Executor>::starttls_relay(&host) {
        Ok(builder) => match try_send(builder, settings.smtp_port, creds, make_message().expect("message đã kiểm tra mailbox")).await {
            Ok(()) => true,
            Err(e) => {
                eprintln!("[quanh-ta] STARTTLS gửi thất bại -> {to}: {e}");
                false
            }
        },
        Err(e) => {
            eprintln!("[quanh-ta] Không dựng được STARTTLS transport -> {to}: {e}");
            false
        }
    }
}
