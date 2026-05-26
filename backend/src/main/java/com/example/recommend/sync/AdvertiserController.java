package com.example.recommend.sync;

import com.example.recommend.security.SecretCipher;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/advertisers")
public class AdvertiserController {

    private final AdvertiserRepository repo;
    private final ProductSyncService sync;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    public AdvertiserController(AdvertiserRepository repo,
                                ProductSyncService sync,
                                org.springframework.jdbc.core.JdbcTemplate jdbc) {
        this.repo = repo;
        this.sync = sync;
        this.jdbc = jdbc;
    }

    public record UpsertRequest(
            @NotBlank String name,
            @NotBlank String hostType,
            @NotBlank String shopUrl,
            String shopKey,
            String licenseKey,
            String notes,
            String selectorName,
            String selectorDetail,
            String selectorPrice,
            String imageAttrs,
            String detailAnchorStart,
            String detailAnchorEnd
    ) {}

    /** API 응답용 — secret 컬럼은 마지막 4자리만 노출, 나머지는 *로 마스킹. */
    private static Advertiser masked(Advertiser a) {
        if (a == null) return null;
        return new Advertiser(
                a.id(),
                a.name(),
                a.hostType(),
                a.shopUrl(),
                SecretCipher.mask(a.shopKey()),
                SecretCipher.mask(a.licenseKey()),
                a.cafe24MallId(),
                SecretCipher.mask(a.cafe24AccessToken()),
                a.notes(),
                a.selectorName(),
                a.selectorDetail(),
                a.selectorPrice(),
                a.imageAttrs(),
                a.detailAnchorStart(),
                a.detailAnchorEnd(),
                a.createdAt(),
                a.updatedAt()
        );
    }

    @GetMapping
    public List<Advertiser> list() {
        return repo.findAll().stream().map(AdvertiserController::masked).toList();
    }

    @GetMapping("/{id}")
    public Advertiser get(@PathVariable long id) {
        return masked(repo.findById(id).orElseThrow());
    }

    @PostMapping
    public Advertiser create(@RequestBody UpsertRequest r) {
        long id = repo.insert(r.name(), r.hostType(), r.shopUrl(), r.shopKey(), r.licenseKey(), r.notes(),
                r.selectorName(), r.selectorDetail(), r.selectorPrice(), r.imageAttrs(),
                r.detailAnchorStart(), r.detailAnchorEnd());
        return masked(repo.findById(id).orElseThrow());
    }

    @PutMapping("/{id}")
    public Advertiser update(@PathVariable long id, @RequestBody UpsertRequest r) {
        repo.update(id, r.name(), r.hostType(), r.shopUrl(), r.shopKey(), r.licenseKey(), r.notes(),
                r.selectorName(), r.selectorDetail(), r.selectorPrice(), r.imageAttrs(),
                r.detailAnchorStart(), r.detailAnchorEnd());
        return masked(repo.findById(id).orElseThrow());
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable long id) {
        repo.delete(id);
        return ResponseEntity.noContent().build();
    }

    /** 동기화 트리거 — 동기 실행 (페이지 200개까지 안전핀). */
    @PostMapping("/{id}/sync")
    public ProductSyncService.SyncResult sync(@PathVariable long id) {
        return sync.syncMakeshop(id);
    }

    /** 동기화 상태 조회. */
    @GetMapping("/{id}/sync-status")
    public List<Map<String, Object>> status(@PathVariable long id) {
        return jdbc.queryForList(
                "SELECT * FROM product_sync_status WHERE advertiser_id = ? AND sync_type = 'PRODUCT_LIST'",
                id
        );
    }
}
