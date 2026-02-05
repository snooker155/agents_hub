# AI-RAN Simulator

The **AI-RAN Simulator** is a full-stack project for simulating and visualizing Open Radio Access Networks (O-RAN) with potential to integrate AI for intelligent control and edge AI service orchestration.

## 🏗️ Project Structure

```
ai-ran-sim/
├── backend/   # Python simulation engine and WebSocket server
├── frontend/  # Next.js web interface for visualization and user interface.
```

## 🚀 Getting Started

### 1. Backend

- Requirements: Python 3.12+, Docker
- Install dependencies:

  ```bash
  pip install -r backend/requirements.txt
  ```

- Start the backend server:

  ```bash
  cd backend
  python main.py
  ```

### 2. Frontend

- Requirements: Node.js (npm)
- Start the frontend:

  ```bash
  cd frontend
  npm install
  npm run dev
  ```

- Open [http://localhost:3000](http://localhost:3000) in your browser.

## 📦 Features

- Basic O-RAN simulation including base stations, cells, user equipments, core, RIC and edge.
- Intelligent xApps for decision-making and monitoring
- Interactive web-based visualization and user chat interface

## 📝 License

MIT License

## 🤝 Contributing

Contributions are welcome! Please open issues or submit pull requests.
