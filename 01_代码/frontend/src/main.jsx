import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {Providers} from './context';
import App from './App';
import './styles.css';
import './detail-styles.css';

createRoot(document.getElementById('root')).render(<BrowserRouter><Providers><App/></Providers></BrowserRouter>);
