import React, {useEffect, useRef} from 'react';
import {Link} from 'react-router-dom';
import {
  Plus, ArrowLeft, ArrowRight, ChevronDown, ChevronUp, Trash2, Check, X, Copy, LockKeyhole,
  Settings2, List, ChartNoAxesColumnIncreasing, Eye, Pencil, LogOut, FileText, Clock3, Users,
  Sparkles, Menu, Search, Circle, CircleDot, SquareCheck, Type, Hash, AlertCircle, ExternalLink,
  MoreHorizontal, CalendarDays, ArrowUpRight, LayoutGrid, CheckCheck, RefreshCw, Link as LinkIcon,
} from 'lucide-react';

const icons = {
  plus: Plus, 'arrow-left': ArrowLeft, 'arrow-right': ArrowRight, 'chevron-down': ChevronDown,
  'chevron-up': ChevronUp, trash: Trash2, check: Check, x: X, copy: Copy, lock: LockKeyhole,
  settings: Settings2, list: List, chart: ChartNoAxesColumnIncreasing, eye: Eye, pencil: Pencil,
  'log-out': LogOut, file: FileText, clock: Clock3, users: Users, sparkles: Sparkles, menu: Menu,
  search: Search, single: CircleDot, multiple: SquareCheck, text: Type, number: Hash,
  'alert-circle': AlertCircle, external: ExternalLink, more: MoreHorizontal, calendar: CalendarDays,
  'arrow-up-right': ArrowUpRight, grid: LayoutGrid, done: CheckCheck, refresh: RefreshCw, link: LinkIcon,
};
export function Icon({name, size = 20, ...props}) {
  const Element = icons[name] || Circle;
  return <Element size={size} strokeWidth={1.8} aria-hidden="true" {...props}/>;
}
export function Button({variant = 'primary', size = '', icon, loading, children, className = '', ...props}) {
  return <button type="button" {...props} disabled={props.disabled || loading} className={`btn btn-${variant} ${size ? `btn-${size}` : ''} ${className}`}>
    {loading ? <span className="spinner"/> : icon ? <Icon name={icon} size={18}/> : null}{children}
  </button>;
}
export function Badge({status, tone, children, className = ''}) {
  const labels = {draft: '草稿', published: '收集中', closed: '已关闭'};
  return <span className={`badge badge-${tone || status || 'neutral'} ${className}`}><span className="badge-dot"/>{children || labels[status] || status}</span>;
}
export function Brand({compact = false}) {
  return <Link className="brand" to="/" aria-label="问序首页"><span className="brand-mark"><i/><i/><i/></span><span>问序{!compact && <small>FORM & FIELD</small>}</span></Link>;
}
export function EmptyState({icon = 'file', title, description, action}) {
  return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={28}/></div><h3>{title}</h3>{description && <p>{description}</p>}{action}</div>;
}
export function Modal({open, title, children, onClose, onConfirm, confirmText = '确认', cancelText = '取消', danger, busy, footer}) {
  const dialog = useRef(null), previousFocus = useRef(null);
  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement;
    dialog.current?.focus();
    const keydown = event => {
      if (event.key === 'Escape' && !busy) onClose?.();
      if (event.key !== 'Tab') return;
      const items = Array.from(dialog.current?.querySelectorAll('button:not(:disabled), input, select, textarea, a[href], [tabindex="0"]') || []);
      if (!items.length) { event.preventDefault(); return; }
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
    };
    document.addEventListener('keydown', keydown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {document.removeEventListener('keydown', keydown); document.body.style.overflow = previousOverflow; previousFocus.current?.focus();};
  }, [open, busy, onClose]);
  if (!open) return null;
  return <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && !busy && onClose?.()}>
    <section className="modal" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="dialog-title" tabIndex={-1}>
      <div className="modal-header"><h2 id="dialog-title">{title}</h2>{onClose && <button className="icon-button" aria-label="关闭对话框" disabled={busy} onClick={onClose}><Icon name="x"/></button>}</div>
      <div className="modal-body">{children}</div>
      <div className="modal-footer">{footer || <><Button variant="secondary" onClick={onClose} disabled={busy}>{cancelText}</Button>{onConfirm && <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={busy}>{confirmText}</Button>}</>}</div>
    </section>
  </div>;
}
export function Loading({label = '正在加载…'}) {return <div className="loading-state"><span className="spinner"/><span>{label}</span></div>;}
