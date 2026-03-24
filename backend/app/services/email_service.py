"""SMTP email helpers."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from backend.app.config import get_settings
from backend.app.models.user import User

logger = logging.getLogger(__name__)


def send_welcome_email(user: User) -> None:
    """Send a welcome email to a newly registered user."""
    settings = get_settings()
    if not settings.email_enabled:
        logger.info("Skipping welcome email because SMTP is not configured.")
        return

    message = EmailMessage()
    message["Subject"] = "Welcome to Study Assistant | Your learning journey starts here"
    message["From"] = f"{settings.mail_from_name} <{settings.effective_mail_from_email}>"
    message["To"] = user.email
    plain_body = "\n".join(
        [
            f"Hi {user.name},",
            "",
            "Welcome to Study Assistant.",
            "Your account has been created successfully, and your personalized learning workspace is now ready.",
            "",
            "You can now begin planning and tracking your preparation with confidence.",
            "",
            "Here is what you can do next:",
            "- Add your subjects and topics",
            "- Generate a smart study schedule",
            "- Take AI-powered quizzes",
            "- Track your learning progress over time",
            "",
            "We are excited to support you throughout your learning journey and help you stay consistent every day.",
            "",
            "Warm regards,",
            "Study Assistant Team",
        ]
    )
    html_body = f"""
    <html>
      <body style="margin:0;padding:0;background:#f5f7fb;font-family:Segoe UI,Arial,sans-serif;color:#1f2937;">
        <div style="max-width:620px;margin:32px auto;padding:0 16px;">
          <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:20px;overflow:hidden;box-shadow:0 18px 40px rgba(15,23,42,0.08);">
            <div style="padding:32px 32px 20px;background:linear-gradient(135deg,#0f172a,#1d4ed8);color:#ffffff;">
              <div style="font-size:13px;letter-spacing:0.12em;text-transform:uppercase;opacity:0.8;">Study Assistant</div>
              <h1 style="margin:12px 0 0;font-size:28px;line-height:1.2;">Welcome, {user.name}</h1>
              <p style="margin:12px 0 0;font-size:15px;line-height:1.7;color:rgba(255,255,255,0.88);">
                Your account has been created successfully, and your personalized learning workspace is ready to go.
              </p>
            </div>
            <div style="padding:28px 32px 32px;">
              <p style="margin:0 0 16px;font-size:15px;line-height:1.7;">
                You can now start organizing your preparation with a smoother, smarter, and more focused workflow.
              </p>
              <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:16px;padding:18px 20px;margin:0 0 20px;">
                <p style="margin:0 0 10px;font-size:14px;font-weight:700;color:#0f172a;">What you can do next</p>
                <ul style="margin:0;padding-left:18px;color:#334155;font-size:14px;line-height:1.9;">
                  <li>Add your subjects and topics</li>
                  <li>Generate a smart study schedule</li>
                  <li>Take Groq-powered quizzes</li>
                  <li>Track your learning progress over time</li>
                </ul>
              </div>
              <p style="margin:0;font-size:15px;line-height:1.7;">
                We are excited to support you throughout your learning journey and help you stay consistent, prepared, and confident.
              </p>
              <p style="margin:24px 0 0;font-size:14px;line-height:1.7;color:#475569;">
                Wishing you success and steady progress.<br /><br />
                Warm regards,<br />
                <strong>Study Assistant Team</strong>
              </p>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except Exception:
        logger.exception("Failed to send welcome email to %s", user.email)
