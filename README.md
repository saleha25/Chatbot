# Empath AI — AI-Powered Mental Health Chatbot

## 1. Project Overview

Empath AI is an AI-powered mental health support chatbot designed to help users express their feelings, reflect on their emotional well-being, and track important wellness indicators such as sleep, mood, and energy levels.

The project aims to make mental health support more accessible through empathetic conversations, wellness tracking, and self-assessment. It combines a Python Flask backend, a web-based interface, AI-based response generation, and integration with the GNU Health ecosystem.

Empath AI explores English and Urdu language support to make mental health conversations more accessible to users with different language preferences.

The system can identify relevant wellness information from conversations and organize it into structured records. Through its GNU Health integration, the project supports workflows for connecting wellness information with the appropriate user profile.

Empath AI provides supportive wellness assistance and is not a replacement for professional mental health care or a clinical diagnosis.

## 2. Project Objectives

The main objectives of Empath AI are:

- Provide an accessible AI-powered mental health chatbot.
- Encourage users to express their emotions in a supportive environment.
- Identify mood and emotional indicators from conversations.
- Track sleep duration, mood, and energy levels.
- Generate wellness summaries from recorded information.
- Provide self-assessment and dashboard functionality.
- Integrate wellness records with GNU Health and MyGNUHealth.
- Explore English and Urdu conversational support.
- Protect users' privacy and sensitive wellness information.

## 3. Key Features

### AI Mental Health Chatbot

Empath AI provides a conversational interface where users can discuss their feelings, daily experiences, stress, and general well-being. The chatbot aims to respond empathetically and encourage healthy reflection.

### Mood and Emotion Detection

The system processes user messages to identify relevant emotional information. Detected information can be organized into structured wellness records for later review.

Emotion detection is an estimate based on text and should not be considered a medical diagnosis.

### Sleep Tracking

The chatbot can identify sleep-duration information when users mention it in conversations. Recorded values can be used to calculate summaries and help users reflect on their sleep habits.

### Energy Tracking

Empath AI supports identifying and recording energy-related information from conversations when sufficient information is available.

### Wellness Dashboard

The dashboard allows users to review available wellness information, including sleep summaries and other recorded indicators.

### Self-Assessment

The self-assessment component supports structured reflection on mental well-being. It can be extended with validated screening questionnaires and appropriate scoring guidance.

### GNU Health Integration

Empath AI integrates with the GNU Health ecosystem through a configured service. The integration supports sending wellness information, creating or transmitting Page of Life records where supported, and associating information with the appropriate user profile.

## 4. Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming and data processing |
| Flask | Web application and API development |
| HTML, CSS, JavaScript | Web interface |
| SQLite | Local database |
| AI response-generation component | Conversational responses |
| GNU Health | Health information ecosystem |
| Thalamus | GNU Health integration service |
| MyGNUHealth | Personal health-record workflow |
| Git and GitHub | Version control and project hosting |

The exact libraries and model dependencies depend on the current project configuration.

## 5. System Architecture

Empath AI follows a modular architecture in which the frontend communicates with the Flask backend. The backend processes user messages, generates chatbot responses, identifies relevant wellness information, and stores records in the SQLite database.

The system also supports integration with the GNU Health ecosystem to transmit supported wellness information and associate it with the appropriate user profile.

The main components are:

- Frontend:Provides the chatbot, wellness dashboard, and self-assessment interfaces.
- Flask Backend: Handles requests, application logic, and data processing.
- AI Response Generation: Generates conversational responses.
- Wellness Processing: Identifies sleep, mood, and energy information from conversations.
- SQLite Database: Stores supported application and wellness records.
- GNU Health Integration: Connects supported wellness information with the configured GNU Health services and MyGNUHealth workflow.

## 6. GNU Health and MyGNUHealth Integration

An important component of Empath AI is its integration with the GNU Health ecosystem.

GNU Health provides health information management capabilities, while MyGNUHealth supports personal health records and self-tracking. Thalamus provides API functionality for supported GNU Health Federation resources.

The integration workflow is designed to:

1. Receive a message from the user.
2. Identify relevant wellness information, such as sleep, mood, and energy.
3. Convert supported information into structured data.
4. Associate the information with the correct user profile.
5. Send supported records to the configured service.
6. Process the response and report synchronization status.
7. Reflect supported information in the corresponding MyGNUHealth workflow.

The development setup has used separate profiles for Izak and Saleha. Keeping their records separate is important for validating the multi-user integration.

Successful synchronization should be verified by checking the API response, record association, and resulting wellness data.

## 7. Project Structure

The following is an illustrative overview of the known project files. Actual file locations may differ.

EMPATH-AI/
│
├── app.py
├── wellness_route.py
├── mental_health_combined.py
├── response_generator.ipynb
├── survey.csv
├── empth_ai.sqlite
├── wellness.html
├── self_assessment.html
├── dashboard.html
├── requirements.txt
└── README.md


### Main Files

- app.py — Main Flask application entry point, depending on the current configuration.
- wellness_route.py — Wellness-related routes and processing.
- mental_health_combined.py— Mental health processing logic.
- response_generator.ipynb — Notebook for experimenting with response generation.
- survey.csv— Survey data used by the project.
- empth_ai.sqlite — SQLite database used by the existing setup.
- wellness.html — Wellness interface.
- self_assessment.html — Self-assessment interface.
- dashboard.html — Dashboard interface.
- requirements.txt — Python dependencies, if maintained in the repository.

Keep the filenames consistent with the actual repository when publishing this documentation.

## 8. Installation and Setup

### Prerequisites

Before running Empath AI, install:

- Python.
- Git.
- A supported web browser.
- The required Python dependencies.
- The configured AI model or response-generation service.
- GNU Health services if integration testing is required.

### Step 1: Clone the Repository

Use the following command with your repository address:

bash
git clone github.com/saleha25/Chatbot


Alternatively, open your existing Empath AI project directory in a terminal.

Note: Standard Git cloning normally requires a valid HTTPS or SSH clone URL. Use the repository's actual clone address if the command above is not accepted.

### Step 2: Check Python

On Windows PowerShell, run:

powershell
py --version


### Step 3: Create a Virtual Environment

powershell
py -m venv venv


Activate it:

powershell
.\venv\Scripts\Activate.ps1


### Step 4: Install Dependencies

Since the project does not currently contain a requirements.txt file, install the Python packages required by your application individually.

For example, if your project uses Flask:

python -m pip install flask

Install any additional packages required by your existing code and configured AI model.

### Step 5: Run the Application

Start the Flask application from the project root:

powershell
py app.py


Open the local address displayed in the terminal in your web browser. The default Flask port is commonly 5000, but the application may use a different port.

If GNU Health integration is required, start the configured service separately according to your installation.

## 9. Wellness Data Processing

Empath AI focuses on three important wellness indicators:

| Indicator | Example | Purpose |
|---|---|---|
| Sleep | 5 hours | Records sleep duration |
| Mood | Low | Records self-reported mood |
| Energy | Low | Records self-reported energy |

For example, a user might say:

“I slept for five hours last night, feel low today, and have very little energy.”

The system may identify the sleep duration, mood, and energy information and use supported values to update wellness records.

The actual extraction results depend on the implemented processing logic. Missing information should remain missing rather than being treated as zero.

Wellness summaries are intended to support personal reflection and are not clinical assessments.

## 10. Privacy and Security

Mental health conversations and wellness records may contain sensitive personal information. Appropriate security measures are essential.

Recommended practices include:

- Never commit passwords, API keys, or access tokens to GitHub.
- Restrict access to personal wellness records.
- Associate each record with the correct user.
- Validate inputs and API responses.
- Avoid storing unnecessary sensitive information.
- Protect credentials used for GNU Health integration.
- Obtain appropriate consent before sharing wellness information.
- Use anonymized or synthetic data for demonstrations.
- Avoid exposing sensitive conversation content in application logs.

A local development environment should not automatically be considered secure enough for production use.

## 11. Limitations

- Emotion detection may misunderstand context or language.
- English and Urdu performance depends on the implemented language-processing methods.
- Wellness extraction may produce incomplete or incorrect results.
- Chatbot responses are not a substitute for professional mental health care.
- GNU Health synchronization depends on service availability, correct configuration, and supported API operations.
- MyGNUHealth visibility depends on the implemented synchronization workflow.

## 12. Future Enhancements

Potential future improvements include:

- More reliable English and Urdu conversational support.
- Secure registration and multi-user onboarding.
- Improved mood and emotion detection.
- Historical sleep, mood, and energy charts.
- Improved synchronization and error handling.
- Stronger user authentication and authorization.
- Evaluation of chatbot responses and wellness-extraction accuracy.
- Improved privacy controls and deployment security.

## 13. License

No project-specific license has been confirmed. Select and add an appropriate license before publicly distributing the source code.

## 14. Acknowledgements

Empath AI uses and explores open-source technologies and the GNU Health ecosystem.

- Python
- Flask
- SQLite
- GNU Health
- MyGNUHealth

---

**Empath AI — Supporting emotional awareness through empathetic conversations and personal wellness tracking.**
