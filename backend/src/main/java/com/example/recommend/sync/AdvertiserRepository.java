package com.example.recommend.sync;

import com.example.recommend.security.SecretCipher;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Repository;

import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class AdvertiserRepository {

    private final JdbcTemplate jdbc;
    private final SecretCipher cipher;

    public AdvertiserRepository(JdbcTemplate jdbc, SecretCipher cipher) {
        this.jdbc = jdbc;
        this.cipher = cipher;
    }

    private final RowMapper<Advertiser> ROW = (rs, rn) -> new Advertiser(
            rs.getLong("id"),
            rs.getString("name"),
            rs.getString("host_type"),
            rs.getString("shop_url"),
            // 암호화 컬럼은 read 시 복호화. legacy plaintext 행은 그대로 통과.
            cipher.decrypt(rs.getString("shop_key")),
            cipher.decrypt(rs.getString("license_key")),
            rs.getString("cafe24_mall_id"),
            cipher.decrypt(rs.getString("cafe24_access_token")),
            rs.getString("notes"),
            rs.getString("selector_name"),
            rs.getString("selector_detail"),
            rs.getString("selector_price"),
            rs.getString("image_attrs"),
            rs.getString("detail_anchor_start"),
            rs.getString("detail_anchor_end"),
            rs.getObject("created_at", OffsetDateTime.class),
            rs.getObject("updated_at", OffsetDateTime.class)
    );

    public List<Advertiser> findAll() {
        return jdbc.query("SELECT * FROM advertisers ORDER BY id DESC", ROW);
    }

    public Optional<Advertiser> findById(long id) {
        return jdbc.query("SELECT * FROM advertisers WHERE id = ?", ROW, id).stream().findFirst();
    }

    public long insert(String name, String hostType, String shopUrl, String shopKey, String licenseKey, String notes,
                       String selectorName, String selectorDetail, String selectorPrice, String imageAttrs,
                       String detailAnchorStart, String detailAnchorEnd) {
        final String encShopKey = cipher.encrypt(shopKey);
        final String encLicenseKey = cipher.encrypt(licenseKey);
        KeyHolder kh = new GeneratedKeyHolder();
        jdbc.update(con -> {
            PreparedStatement ps = con.prepareStatement(
                    "INSERT INTO advertisers (name, host_type, shop_url, shop_key, license_key, notes, " +
                            "selector_name, selector_detail, selector_price, image_attrs, detail_anchor_start, detail_anchor_end) " +
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    Statement.RETURN_GENERATED_KEYS);
            ps.setString(1, name);
            ps.setString(2, hostType);
            ps.setString(3, shopUrl);
            ps.setString(4, encShopKey);
            ps.setString(5, encLicenseKey);
            ps.setString(6, notes);
            ps.setString(7, selectorName);
            ps.setString(8, selectorDetail);
            ps.setString(9, selectorPrice);
            ps.setString(10, imageAttrs);
            ps.setString(11, detailAnchorStart);
            ps.setString(12, detailAnchorEnd);
            return ps;
        }, kh);
        Number key = (Number) kh.getKeys().get("id");
        return key.longValue();
    }

    public void update(long id, String name, String hostType, String shopUrl, String shopKey, String licenseKey, String notes,
                       String selectorName, String selectorDetail, String selectorPrice, String imageAttrs,
                       String detailAnchorStart, String detailAnchorEnd) {
        jdbc.update(
                "UPDATE advertisers SET name = ?, host_type = ?, shop_url = ?, shop_key = ?, license_key = ?, notes = ?, " +
                        "selector_name = ?, selector_detail = ?, selector_price = ?, image_attrs = ?, " +
                        "detail_anchor_start = ?, detail_anchor_end = ?, updated_at = NOW() WHERE id = ?",
                name, hostType, shopUrl, cipher.encrypt(shopKey), cipher.encrypt(licenseKey), notes,
                selectorName, selectorDetail, selectorPrice, imageAttrs,
                detailAnchorStart, detailAnchorEnd, id);
    }

    public void delete(long id) {
        jdbc.update("DELETE FROM advertisers WHERE id = ?", id);
    }
}
