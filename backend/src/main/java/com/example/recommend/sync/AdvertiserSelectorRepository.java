package com.example.recommend.sync;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;

@Repository
public class AdvertiserSelectorRepository {

    private final JdbcTemplate jdbc;

    public AdvertiserSelectorRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private static final RowMapper<AdvertiserSelector> ROW = (rs, rn) -> new AdvertiserSelector(
            rs.getLong("id"),
            rs.getLong("advertiser_id"),
            rs.getString("role"),
            rs.getString("selector"),
            (Integer) rs.getObject("priority"),
            (Boolean) rs.getObject("enabled"),
            rs.getString("notes"),
            rs.getObject("created_at", OffsetDateTime.class),
            rs.getObject("updated_at", OffsetDateTime.class)
    );

    public List<AdvertiserSelector> findAll(long advertiserId) {
        return jdbc.query(
                "SELECT * FROM advertiser_selectors WHERE advertiser_id = ? ORDER BY role, priority, id",
                ROW, advertiserId
        );
    }

    public List<AdvertiserSelector> findEnabled(long advertiserId, String role) {
        return jdbc.query(
                "SELECT * FROM advertiser_selectors WHERE advertiser_id = ? AND role = ? AND enabled = TRUE ORDER BY priority, id",
                ROW, advertiserId, role
        );
    }

    public long insert(long advertiserId, String role, String selector, Integer priority, Boolean enabled, String notes) {
        return jdbc.queryForObject(
                "INSERT INTO advertiser_selectors (advertiser_id, role, selector, priority, enabled, notes) " +
                        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                Long.class,
                advertiserId, role, selector,
                priority == null ? 100 : priority,
                enabled == null ? Boolean.TRUE : enabled,
                notes
        );
    }

    public void update(long id, String selector, Integer priority, Boolean enabled, String notes) {
        jdbc.update(
                "UPDATE advertiser_selectors SET selector = ?, priority = ?, enabled = ?, notes = ?, updated_at = NOW() WHERE id = ?",
                selector, priority == null ? 100 : priority, enabled == null ? Boolean.TRUE : enabled, notes, id
        );
    }

    public void delete(long id) {
        jdbc.update("DELETE FROM advertiser_selectors WHERE id = ?", id);
    }

    /** 한 광고주의 selector를 전체 교체 (수정 폼에서 N개를 통째로 보낼 때 사용). */
    public void replaceAll(long advertiserId, List<AdvertiserSelector> items) {
        jdbc.update("DELETE FROM advertiser_selectors WHERE advertiser_id = ?", advertiserId);
        for (AdvertiserSelector s : items) {
            if (s.selector() == null || s.selector().isBlank()) continue;
            insert(advertiserId, s.role(), s.selector(),
                    s.priority(), s.enabled() == null ? Boolean.TRUE : s.enabled(), s.notes());
        }
    }
}
