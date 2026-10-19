| 세션 | 위치 | 라벨          | 행동                                                           | 수집 시간 |
| ---- | ---- | ------------- | -------------------------------------------------------------- | --------- |
| S01  | P0   | empty         | 방 안에 아무도 없음                                            | 180초     |
| S02  | P1   | static_person | TX-RX 사이 중앙에서 가만히 서 있기                             | 180초     |
| S03  | P1   | moving_person | TX-RX 사이 중앙을 기준으로 천천히 왕복 걷기                    | 180초     |
| S04  | P0   | empty         | 방 안에 아무도 없음, guided recalibration용 재측정             | 300초     |
| S05  | P1   | static_person | recalibration 이후 TX-RX 사이 중앙에서 가만히 서 있기          | 180초     |
| S06  | P1   | moving_person | recalibration 이후 TX-RX 사이 중앙을 기준으로 천천히 왕복 걷기 | 180초     |
| S07  | P0   | empty         | 방 안에 아무도 없음, 최종 empty 재확인                         | 180초     |
