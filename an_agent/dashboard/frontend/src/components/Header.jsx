import React from 'react';
import { Bot } from 'lucide-react';

const Header = ({ connected }) => {
  return (
    <nav className="navbar navbar-dark bg-dark sticky-top shadow-sm px-4">
      <div className="container-fluid">
        <a className="navbar-brand d-flex align-items-center" href="#">
          <Bot className="me-2" /> AN-Agent Dashboard (React)
        </a>
        <div className={`badge ${connected ? 'bg-success' : 'bg-danger'}`}>
          {connected ? 'Connected' : 'Disconnected'}
        </div>
      </div>
    </nav>
  );
};

export default Header;
