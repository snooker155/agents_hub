# AI-Powered Software Development Factory

This project is an automated system for software development, utilizing multiple AI agents to handle different stages of the development lifecycle: from initial requirements gathering to code implementation and testing.

## Project Purpose
The goal of this project is to streamline the software development process by:
- Automating business analysis and technical specification generation.
- Automatically breaking down projects into actionable development tasks.
- Generating backend and frontend code using LLMs.
- Providing automated DevOps and QA artifacts.

## Architecture
The system is composed of several specialized agents:
- **PM (Project Manager)**: Handles project intake and clarifying questions.
- **BA (Business Analyst)**: Generates Business Requirements Documents (BRD).
- **SD (Solution Designer)**: Creates technical specifications, OpenAPI contracts, and data models.
- **TL (Team Lead)**: Selects the technology stack and splits the project into tasks.
- **Dev (Backend/Frontend/DevOps)**: Implements the code and configuration.
- **QA (Quality Assurance)**: Generates automated tests.

## Dashboard GUI
A web-based dashboard is provided for visual representation and management of the agent processes.

### How to use the Dashboard

1. **Start the Backend:**
   ```bash
   python dashboard/backend/main.py
   ```
   The backend will run on `http://localhost:8001`.

2. **Start the Frontend:**
   ```bash
   cd dashboard/frontend
   npm run dev
   ```
   The frontend will run on `http://localhost:3000`.

3. **Managing Processes:**
   - Enter a project description in the "Agent Controls" section and click "Run Full Graph" or "PM Intake".
   - Monitor tasks in the "Task Board" and logs in the "Interaction Logs".
   - If user input is required (e.g., PM questions), a form will appear in the dashboard for you to submit answers.
   - View generated artifacts in the `out/` directory.

## Core Directives
- Tasks are managed via `out/plan/tasks.json`.
- Logs are stored in `out/logs/interaction_log.json`.
- All generated code and documentation are located in the `out/` folder.
