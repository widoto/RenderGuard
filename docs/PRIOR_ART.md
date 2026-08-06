# Prior Art Survey

> 조사일: 2026-07-17 | 확인되지 않은 항목은 "미확인"으로 표기

---

## 1. PDF 구조 기반 탐지 도구

| 항목 | PhantomLint | pdf-injection-scanner | Doc-Sherlock | PDF-Prompt-Injection-Toolkit |
|------|-------------|----------------------|--------------|------------------------------|
| **출처** | [arXiv:2508.17884](https://arxiv.org/abs/2508.17884), [GitHub](https://github.com/tobycmurray/phantom-lint) | [GitHub](https://github.com/Andy8647/pdf-injection-scanner), [DEV Blog](https://dev.to/andy8647/i-built-a-tool-to-detect-hidden-prompt-injections-in-pdfs-heres-what-i-learned-4hbg) | [GitHub](https://github.com/robomotic/doc-sherlock) | [GitHub](https://github.com/zhihuiyuze/PDF-Prompt-Injection-Toolkit) |
| **저자** | Toby Murray (U. Melbourne) | Andy8647 | Paolo Di Prodi (robomotic) | Gaoxing Zhang (zhihuiyuze) |
| **탐지 대상** | 9가지: 배경색 일치, OCG/Tr3, 초소형폰트, z-order 가림, 페이지 밖, 클리핑, 투명도, 악성폰트, CSS hidden | 4가지: 백색/근백색 텍스트, 초소형폰트(<2pt), 페이지 밖 좌표, 정규식 패턴 매칭 | 9가지: 저명도비 텍스트, 초소형폰트(<4pt), 페이지 밖, 저투명도, OCG 히든레이어, z-order 가림, 메타데이터, 렌더/OCR 불일치, 인코딩 이상 | 공격 6가지 + 탐지 7가지. 탐지: 백색텍스트, 초소형폰트(<3pt), 메타데이터, 페이지 밖, 제로폭 유니코드, OCG 히든, 추출 비교, 패턴 매칭 |
| **탐지 신호** | 구조적 텍스트 추출 → 의심 구문 시맨틱 매칭(MiniLM-L6-v2, 10개 기본 구문) → 해당 영역 렌더링 → Tesseract OCR → 구조/OCR 차이 탐지 | PDF 문자별 `non_stroking_color` > 0.9 임계값, 폰트 크기 < 2pt, 좌표가 페이지 범위 밖, 30+개 정규식 패턴 | WCAG 명도비 기반 색상 분석, 폰트 크기 임계값, 좌표 범위 검사, 투명도 검사, OCG 가시성, z-order 분석, OCR 대조(Tesseract) | 0-100 위험 점수 산출. 색상/크기/위치/OCG/유니코드/추출비교/정규식 18+개 패턴 |
| **성능 (Recall)** | 합성 26건 100%, 실제 113건 100% | 실제 15건 100% (소규모 자체 테스트) | 미확인 (벤치마크 미공개) | 미확인 (벤치마크 미공개, 로드맵 항목) |
| **성능 (FP)** | ICML 2025 3,257건 중 3건 (0.092%) | 자체 테스트 "0건" (소규모) | 미확인 | 미확인 |
| **성능 (지연)** | 학술논문 68.25s/건, CV 43.75s/건 | 미확인 | 미확인 | 미확인 |
| **명시된 한계** | (1) 긴 텍스트 블록에서 FN 가능, (2) OCR이 #fefefe 같은 미세 색상도 일부 읽어버림, (3) 구문 목록 10개 의존, (4) 기준선 비교 없음, (5) 적대적 회피 미논의 | (1) Render Mode 3 우회 가능, (2) OCG OFF 우회 가능, (3) 임베디드 JS 미탐지, (4) ML 분류기 실전 recall 15-41%로 포기 | (소스 확인) (1) 배경색 (255,255,255) 하드코딩 — 비백색 배경에서 명도비 오계산, (2) WCAG AA 4.5:1 기본값 — #808080(3.95:1) 등 정상 회색도 오탐 대상, (3) word 단위 분석 — per-glyph 색상 변화 미감지, (4) opacity 탐지는 정규식으로 ca/CA 연산자 파싱 — 구조적 파싱 아님 | 정확도 벤치마크 미실시, 정규식 기반 한계, PDF 전용(docx 미지원) |
| **라이선스** | BSD-3-Clause | MIT | AGPL-3.0 | MIT |
| **마지막 커밋** | 2025-10-23 (86 commits) | 2026-04-10 (6 commits) | 2025-11-12 | 2026-03-26 (4 commits) |

### 색상 탐지 방식 상세 비교

| 도구 | 색상 임계값 방식 | WCAG sRGB 선형화 | per-glyph 분석 | 근백색 그라데이션 대응 |
|------|-----------------|------------------|---------------|---------------------|
| PhantomLint | 직접 색상 검사 안 함 (OCR 대조에 의존) | 해당 없음 | 아니오 | OCR이 #fefefe도 읽는 경우 미탐 가능 (논문에서 인정) |
| pdf-injection-scanner | RGB 각 채널 > 0.9 고정 임계값 | 아니오 | 아니오 | 0.9 이하 값(예: 0.89)은 통과 |
| Doc-Sherlock | WCAG AA 4.5:1 명도비 (기본값). `contrast_detector.py:56` | **예** (`contrast_detector.py:34`, 임계값 0.04045 사용) | 아니오 (`extract_words()` 워드 단위) | 배경을 `(255,255,255)` 하드코딩(`contrast_detector.py:100`). 비백색 배경 문서에서 명도비 오계산. 4.5:1 임계값이 정상 회색 텍스트(#808080=3.95:1, #999999=2.85:1)도 오탐 |
| PDF-Prompt-Injection-Toolkit | 미확인 | 미확인 | 아니오 | 미확인 |

---

## 2. 학술 논문 — PDF 은닉/인젝션

### 2-A. arXiv:2605.28999 — 이력서 스크리닝 HCD/VDA

| 항목 | 내용 |
|------|------|
| **제목** | Measuring Real-World Prompt Injection Attacks in LLM-based Resume Screening |
| **저자** | Mohan Zhang, Yuqi Jia, Zhen Tan, Steven Jiang, Neil Zhenqiang Gong, Tianlong Chen, Dawn Song |
| **학회** | USENIX Security 2026 |
| **출처** | [arXiv](https://arxiv.org/abs/2605.28999), [GitHub](https://github.com/UNITES-Lab/resume-injection-measurement) (MIT) |
| **탐지 대상** | 색상 일치 텍스트, 초소형 폰트, 페이지 밖 텍스트, PDF 레이어 조작 |
| **탐지 신호** | **HCD**: 규칙 기반 시각 분석(폰트 크기, 색상 거리, 시각 분산, 잉크 밀도) → LLM 시맨틱 검증 캐스케이드. **VDA**: 렌더 이미지 vs 추출 텍스트를 비전-언어 모델로 비교 |
| **성능** | HCD: precision 86.1%, 지연 1.35s/건, 비용 $0.0001/건. VDA: precision 92.7%, 지연 24.82s/건, 비용 $0.0134/건. HCD가 VDA 대비 18x 빠름, 134x 저렴. **Recall 미측정** (유병률 ~1%는 하한) |
| **한계** | (1) Recall 미측정, (2) 탐지된 공격의 실제 스크리닝 영향 미평가, (3) 이력서 데이터 비공개, (4) VDA는 20+ 페이지 문서에서 할루시네이션, (5) 단일 플랫폼(hireEZ) |
| **라이선스** | MIT (코드), CC BY 4.0 (논문) |

### 2-B. arXiv:2606.15020 — Render/Extract 갭 카탈로그

| 항목 | 내용 |
|------|------|
| **제목** | Semantic Integrity Failures in Document-to-LLM Supply Chains |
| **저자** | Side Liu, Jiang Ming (Tulane University) |
| **출처** | [arXiv](https://arxiv.org/abs/2606.15020) |
| **탐지 대상** | 25개 Extraction Gap (EG01-EG25): 시맨틱 오버라이드(ToUnicode/ActualText), 히든 인젝션(Tr3/투명도/페이지밖/초소형폰트/백색텍스트/클리핑/OCG/Tr7/제로높이/반전클리핑/중첩OCG), 리딩오더 분할, 폰트디코딩 분할 |
| **탐지 신호** | 정적 PDF 구조 분석: 렌더링 모드, /ActualText·/ToUnicode 불일치, OCG 가시성, 폰트 메타데이터 무결성. "any finding" / "high-confidence finding" 2단계 스코어 |
| **성능** | 자체 카탈로그 25건 100% 탐지. 실문서 FP: 시맨틱 오버라이드 0.02-0.46%, 히든 인젝션 0.04-0.28%(high-conf), 리딩오더 0-1.53%, 폰트디코딩 6.81%(학술논문 LaTeX Type3) |
| **한계** | (1) 블랙박스 배포 시 파서 버전 귀속 불가, (2) OCR 방어는 긴 문서에서 실패(Gemini: 80p OK, 120p 실패), (3) 폰트디코딩 판정은 렌더/추출 확인 필요, (4) 가시적 텍스트 인젝션·jailbreak·JS 범위 밖 |
| **코드** | **공개 저장소 미발견** |
| **라이선스** | arXiv 비독점 배포 라이선스 |

### 2-C. 추가 관련 논문

| 논문 | 초점 | 핵심 기여 | 출처 |
|------|------|----------|------|
| Invisible Prompts, Visible Threats (EMNLP 2025 Findings) | 악성 폰트 인젝션 — CMap 리매핑으로 글리프 정체성 변경 | PDF에서 공격 성공률 63-70%, 카피-페이스트에도 강건 | [arXiv:2505.16957](https://arxiv.org/abs/2505.16957) |
| PDFuzz (arXiv 2508.01887) | 문자 스트림 순서 조작 — 시각적으로 동일하나 추출 텍스트 스크램블 | ArguGPT 탐지 93.6% → 50.4% 완전 회피 | [arXiv:2508.01887](https://arxiv.org/abs/2508.01887), [GitHub](https://github.com/ACMCMC/PDFuzz) |
| Story Beyond the Eye (PETS 2023) | per-glyph 위치 데이터로 교정 텍스트 복원 | 11개 PDF 교정 도구의 정보 유출 입증 | [arXiv:2206.02285](https://arxiv.org/abs/2206.02285) |
| FontCode (2017) | 글리프 형태 교란 기반 문서 스테가노그래피 | 폰트 매니폴드 상 per-glyph 변형 | [arXiv:1707.09418](https://arxiv.org/abs/1707.09418) |
| Prompt Injection in Resume Screening (arXiv 2606.27287) | 이력서 LLM 스크리닝에서 인젝션 효과 측정 | 인젝션이 랭킹 역전 유발, 광범위 사용 시 효과 소멸 | [arXiv:2606.27287](https://arxiv.org/abs/2606.27287) |
| PARSE (arXiv 2606.17467) | 도메인 인식 검색 데이터 정화 | 공격 성공률 15.6%로 감소, 유틸리티 86.9% 유지 | [arXiv:2606.17467](https://arxiv.org/pdf/2606.17467) |

---

## 3. 텍스트/모델 레벨 탐지 (PDF 구조 비인식)

| 도구/논문 | 방식 | PDF 구조 인식 | 색상 분석 | 출처 |
|-----------|------|--------------|----------|------|
| ProtectAI DeBERTa v2 | 파인튜닝 DeBERTa 분류기 | 아니오 | 아니오 | [HuggingFace](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2) |
| Meta Prompt Guard 2 | mDeBERTa 다국어 분류기 | 아니오 | 아니오 | [HuggingFace](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M) |
| Rebuff | 휴리스틱 + LLM + VectorDB | 아니오 | 아니오 | [GitHub](https://github.com/protectai/rebuff) |
| Attention Tracker (NAACL 2025) | 어텐션 패턴 추적 | 아니오 | 아니오 | [arXiv:2411.00348](https://arxiv.org/abs/2411.00348) |
| PIShield (arXiv 2510.14005) | 잔차 스트림 선형 분류 | 아니오 | 아니오 | [arXiv:2510.14005](https://arxiv.org/abs/2510.14005) |
| PISanitizer (arXiv 2511.10720) | 고어텐션 토큰 정화 | 아니오 | 아니오 | [arXiv:2511.10720](https://arxiv.org/abs/2511.10720) |
| DataFilter (arXiv 2510.19207) | SFT 기반 악성 지시 필터링 | 아니오 | 아니오 | [arXiv:2510.19207](https://arxiv.org/abs/2510.19207) |

---

## 4. 기타 관련 도구

| 도구 | 유형 | 설명 | 출처 |
|------|------|------|------|
| OpenDataLoader PDF | 파서+필터 | AI safety 필터 내장 PDF 파서. 투명도/크기/위치/OCG 필터링. Apache 2.0 | [GitHub](https://github.com/opendataloader-project/opendataloader-pdf) |
| Elysiatools Scanner | 온라인 도구 | 이중 추출(필터 on/off) 비교. 벤치마크 미공개 | [Web](https://elysiatools.com/en/tools/pdf-prompt-injection-scanner) |
| Firecrawl pdf-inspector | Rust 파서 | PDF 타입 분류(텍스트/스캔/혼합). 보안 미특화. 300+p PDF 수 ms | [GitHub](https://github.com/firecrawl/pdf-inspector) |
| Glasswall CDR | 상용 CDR | 파일 구조 검증·재구축. 맬웨어 중심, 프롬프트 인젝션 미특화 | [Web](https://www.glasswall.com/) |
| OPSWAT Deep CDR | 상용 CDR | 200+ 파일 타입 정화·재구축. 프롬프트 인젝션 미특화 | [Web](https://www.opswat.com/technologies/deep-cdr) |

---

## 5. 공격 전용 도구 (레드팀)

| 도구 | 설명 | 출처 |
|------|------|------|
| Inject My PDF (Kai Greshake) | 브라우저 기반 백색 텍스트 인젝션, 교육 목적 | [Web](https://kai-greshake.de/posts/inject-my-pdf/) |
| Sneaky PDF | 브라우저 기반 1pt 컬러 텍스트 삽입 | [Web](https://sneaky-pdf.com/) |
| PDF_prompt_injector (barkx) | 스테가노그래피/OCR텍스트/메타데이터 인젝션 | [GitHub](https://github.com/barkx/PDF_prompt_injector) |

---

## 6. Per-Glyph 미세 색상 그라데이션 공격에 대한 선행 연구

**검색 수행 내역:**
- "per-glyph color gradient attack PDF injection"
- "micro color gradient text attack evasion PDF"
- "character level color manipulation PDF steganography"
- "near-white text detection PDF threshold bypass"
- "PDF text color close to white threshold evasion detector bypass"

**결론: 해당 공격 기법을 다룬 선행 연구를 발견하지 못했다.**

구체적으로 확인한 사항:

| 기존 연구 | 관련성 | 차이점 |
|-----------|--------|--------|
| FontCode (2017) | per-glyph 변형이지만 **형태** 교란 | 색상이 아닌 글리프 모양 변형 |
| Invisible Prompts (EMNLP 2025) | per-glyph CMap 리매핑 | 색상이 아닌 글리프 정체성 변경 |
| Story Beyond the Eye (PETS 2023) | per-glyph 위치 분석 | 색상이 아닌 위치 데이터 |
| PDFuzz (2025) | 문자 단위 조작 | 색상이 아닌 스트림 순서 변경 |
| pdf-injection-scanner | 색상 임계값(>0.9) | 고정 임계값; per-glyph 분석 없음 |
| PhantomLint | OCR 기반 간접 색상 탐지 | #fefefe 수준에서 OCR이 읽어버려 미탐 인정 |

**기존 도구의 색상 탐지 한계:**
- `pdf-injection-scanner`: RGB 각 채널 > 0.9 고정 임계값 (`scanner.py:72`). 0.9 이하 근백색(예: #E5E5E5=0.898)은 통과
- `pdf-injection-scanner`: 배경색 대조 `is_same_as_bg()`는 채널 차이 < 0.05 (`scanner.py:98-100`). 차이가 0.05 이상이면 통과
- `PhantomLint`: OCR에 의존 → Tesseract가 희미한 텍스트도 읽는 경우 미탐 (논문에서 인정)
- `Doc-Sherlock`: sRGB 선형화 포함 확인됨. 단, 배경을 백색 하드코딩하므로 비백색 배경에서 오계산. WCAG AA 4.5:1 임계값이 정상 회색 텍스트에도 오탐 발생
- **어떤 도구도 per-glyph 색상 분산(variance)을 탐지 신호로 사용하지 않음**

---

## 7. 종합 비교: 탐지 기법 커버리지

| 은닉 기법 | PhantomLint | pdf-injection-scanner | Doc-Sherlock | 2605.28999 HCD | 2606.15020 |
|-----------|-------------|----------------------|--------------|----------------|------------|
| White-on-white | O (OCR) | O (>0.9 임계값) | O (명도비 4.5:1) | O (색상 거리) | O (EG08) |
| 근백색(0.9 이하, 예: #E5E5E5) | 미확인 (OCR 의존) | **X** (임계값 통과) | O (명도비 1.26:1 < 4.5) | 미확인 | **X** (EG08은 백색만) |
| 비백색 배경 위 은닉(예: #E0E0E0 on #E5E5E5) | 미확인 | **X** (두 값 모두 < 0.9) | **X** (배경 백색 가정) | 미확인 | 미확인 |
| 투명도(ca<0.1) | O | X | O (min_opacity=0.3) | 미확인 | O (EG05) |
| BM(Screen/Lighten) | 미확인 | X | X | 미확인 | 미확인 |
| Tr 3 invisible render | O | X | 미확인 | 미확인 | O (EG04) |
| 초소형 폰트(<2pt) | O | O (<2pt) | O (<4pt) | O | O (EG07) |
| 불투명 박스 덮기 | O (z-order) | X | O (z-order) | 미확인 | 미확인 |
| 페이지 crop box 밖 | O | O | O | O | O (EG06) |
| WCAG sRGB 선형화 | 해당없음 | X | O (확인됨) | X | 해당없음 |
| 정상 회색 텍스트 오탐 | 미확인 | X (해당없음) | **예상됨** (#808080=3.95:1 < 4.5) | 미확인 | X (해당없음) |

---

*이 문서는 웹 검색 결과를 기반으로 작성되었으며, 각 항목의 출처 URL을 명기했다. 미확인 항목은 추가 검증이 필요하다.*
