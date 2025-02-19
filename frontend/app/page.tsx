"use client";

import { useEffect, useState } from "react";

export default function Home() {
    const [message, setMessage] = useState("読み込み中...");

    useEffect(() => {
        fetch("http://127.0.0.1:8000/") // ← `127.0.0.1` に統一
            .then((res) => {
                if (!res.ok) {
                    throw new Error("サーバーエラー");
                }
                return res.json();
            })
            .then((data) => setMessage(data.message))
            .catch((error) => {
                console.error("API通信エラー:", error);
                setMessage("APIからのデータ取得に失敗しました");
            });
    }, []);

    return (
        <div>
            <h1>ストアランキングアプリ</h1>
            <p>APIレスポンス: {message}</p>
        </div>
    );
}
