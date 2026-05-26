package com.example.recommend.security;

import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;

/**
 * AES-GCM 기반의 application-layer 컬럼 암호화 헬퍼.
 *
 * 광고주 테이블의 license_key / shop_key / cafe24_*_token 같은 secret 컬럼에 사용한다.
 * 포맷: {@code enc:v1:<base64(nonce || ciphertext || tag)>}
 *
 * 키 미설정(dev)인 경우 plaintext passthrough — 시작 시 WARN 로그.
 * 프로덕션에서는 {@code APP_SECRET_KEY} 환경변수(32바이트 base64)를 반드시 설정해야 한다.
 */
@Component
public class SecretCipher {

    private static final Logger log = LoggerFactory.getLogger(SecretCipher.class);
    private static final String PREFIX = "enc:v1:";
    private static final int NONCE_BYTES = 12;
    private static final int TAG_BITS = 128;

    private final SecretKey key;
    private final SecureRandom random = new SecureRandom();

    public SecretCipher(@Value("${app.security.encryption-key:}") String keyB64) {
        if (keyB64 == null || keyB64.isBlank()) {
            this.key = null;
        } else {
            byte[] bytes;
            try {
                bytes = Base64.getDecoder().decode(keyB64.trim());
            } catch (IllegalArgumentException e) {
                throw new IllegalStateException(
                        "app.security.encryption-key 가 valid base64 가 아닙니다.", e);
            }
            if (bytes.length != 16 && bytes.length != 24 && bytes.length != 32) {
                throw new IllegalStateException(
                        "app.security.encryption-key 길이가 16/24/32 바이트가 아닙니다. 받은 길이=" + bytes.length);
            }
            this.key = new SecretKeySpec(bytes, "AES");
        }
    }

    @PostConstruct
    void logKeyState() {
        if (key == null) {
            log.warn("[SecretCipher] APP_SECRET_KEY 미설정 — 광고주 secret 컬럼을 평문으로 저장합니다. " +
                    "프로덕션 배포 전에 32바이트(base64) 키를 반드시 설정하세요.");
        } else {
            log.info("[SecretCipher] AES-GCM 키 로드 완료 (length={} bytes)", key.getEncoded().length);
        }
    }

    /** 평문이면 암호화, 이미 enc:v1: prefix면 그대로 반환. null/blank는 그대로. */
    public String encrypt(String plaintext) {
        if (plaintext == null || plaintext.isEmpty()) return plaintext;
        if (plaintext.startsWith(PREFIX)) return plaintext;
        if (key == null) return plaintext; // dev passthrough
        try {
            byte[] nonce = new byte[NONCE_BYTES];
            random.nextBytes(nonce);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, nonce));
            byte[] ct = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
            byte[] out = new byte[nonce.length + ct.length];
            System.arraycopy(nonce, 0, out, 0, nonce.length);
            System.arraycopy(ct, 0, out, nonce.length, ct.length);
            return PREFIX + Base64.getEncoder().encodeToString(out);
        } catch (Exception e) {
            throw new IllegalStateException("AES-GCM 암호화 실패", e);
        }
    }

    /**
     * enc:v1: prefix면 복호화, 아니면 (legacy plaintext) 그대로 반환.
     * 키 미설정 상태인데 prefix가 있는 값을 만나면 IllegalStateException — 데이터 손실 방지.
     */
    public String decrypt(String stored) {
        if (stored == null || stored.isEmpty()) return stored;
        if (!stored.startsWith(PREFIX)) return stored;
        if (key == null) {
            throw new IllegalStateException(
                    "암호화된 값(enc:v1:)을 만났지만 APP_SECRET_KEY 가 설정되지 않았습니다.");
        }
        try {
            byte[] raw = Base64.getDecoder().decode(stored.substring(PREFIX.length()));
            if (raw.length < NONCE_BYTES + 16) {
                throw new IllegalStateException("암호문 길이가 너무 짧습니다.");
            }
            byte[] nonce = new byte[NONCE_BYTES];
            byte[] ct = new byte[raw.length - NONCE_BYTES];
            System.arraycopy(raw, 0, nonce, 0, NONCE_BYTES);
            System.arraycopy(raw, NONCE_BYTES, ct, 0, ct.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, nonce));
            return new String(cipher.doFinal(ct), StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("AES-GCM 복호화 실패", e);
        }
    }

    /** 응답용 마스킹. 끝 4자만 노출하고 나머지는 *. null/blank는 그대로. */
    public static String mask(String value) {
        if (value == null || value.isEmpty()) return value;
        int n = value.length();
        if (n <= 4) return "*".repeat(n);
        int keep = Math.min(4, n / 4 + 1);
        return "*".repeat(n - keep) + value.substring(n - keep);
    }
}
