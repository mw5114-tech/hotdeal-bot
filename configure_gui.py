"""Owner-operated local secret entry. Never logs values or sends a message."""
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from raw_runtime import load_env
from raw_delivery import Bot, targets


def main():
    rootdir = Path(__file__).resolve().parent
    envfile = rootdir / '.env'
    load_env(envfile)
    window = tk.Tk()
    window.title('핫딜 봇 — 안전한 로컬 인증값 입력')
    window.geometry('650x470')
    window.attributes('-topmost', True)
    tk.Label(window, text='기존 봇 토큰과 개인 Chat ID를 이 PC의 입력창에만 넣으세요.\n채팅이나 화면 캡처로 보내지 마세요. 저장 시 봇/대상을 확인하며 메시지는 전송하지 않습니다.', justify='left').pack(padx=18,pady=15)
    tk.Label(window, text='기존 Telegram API ID (my.telegram.org 발급값)').pack(anchor='w',padx=18)
    api_id = tk.Entry(window, show='*', width=75)
    api_id.insert(0,os.environ.get('TELEGRAM_API_ID',''))
    api_id.pack(padx=18,pady=6)
    tk.Label(window, text='기존 Telegram API Hash').pack(anchor='w',padx=18)
    api_hash = tk.Entry(window, show='*', width=75)
    api_hash.insert(0,os.environ.get('TELEGRAM_API_HASH',''))
    api_hash.pack(padx=18,pady=6)
    tk.Label(window, text='기존 이 가격 못 참지 BOT 토큰').pack(anchor='w',padx=18)
    token = tk.Entry(window, show='*', width=75)
    token.pack(padx=18,pady=6)
    tk.Label(window, text='내 개인 TELEGRAM_CHAT_ID (양수 숫자)').pack(anchor='w',padx=18)
    owner = tk.Entry(window, show='*', width=75)
    owner.pack(padx=18,pady=6)
    status = tk.Label(window, text='그룹: 핫딜 MUSE용 — 기존 설정 재사용')
    status.pack(pady=8)

    def save():
        t, c = token.get().strip(), owner.get().strip()
        aid,ahash=api_id.get().strip(),api_hash.get().strip()
        if not aid.isdigit() or len(ahash)!=32 or ':' not in t or any(ch.isspace() for ch in t+ahash) or not c.isdigit() or int(c)<=0:
            messagebox.showerror('입력 확인','토큰과 양수 Chat ID 형식을 확인하세요.')
            return
        os.environ['TELEGRAM_BOT_TOKEN'] = t
        os.environ['TELEGRAM_CHAT_ID'] = c
        status.config(text='기존 봇과 개인방·그룹 확인 중…')
        window.update_idletasks()
        try:
            Bot().check_targets(targets())
            lines = envfile.read_text('utf-8').splitlines()
            values = dict(line.split('=',1) for line in lines if line and not line.startswith('#'))
            values.update(TELEGRAM_BOT_TOKEN=t,TELEGRAM_CHAT_ID=c,TELEGRAM_API_ID=aid,TELEGRAM_API_HASH=ahash)
            # Keep the already-restricted file's Windows ACL; do not replace it.
            with envfile.open('w',encoding='utf-8',newline='\n') as f:
                f.write(''.join(k+'='+v+'\n' for k,v in values.items()))
            (rootdir/'credentials-ready.json').write_text(json.dumps({'ready':True,'destinations':2}),encoding='utf-8')
        except Exception:
            status.config(text='확인 실패: 토큰·Chat ID·그룹 권한 또는 네트워크를 확인하세요. 값은 출력하지 않았습니다.')
            return
        token.delete(0,'end');owner.delete(0,'end')
        messagebox.showinfo('완료','기존 봇과 두 대상을 확인했습니다. 인증값을 로컬에 저장했습니다. 메시지는 아직 보내지 않았습니다.')
        window.destroy()

    tk.Button(window,text='확인 후 안전하게 저장',command=save).pack(pady=8)
    window.mainloop()


if __name__=='__main__':
    main()
