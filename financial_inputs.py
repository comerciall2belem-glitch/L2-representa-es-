from typing import Literal
from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

class FinancialInput(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    kind: Literal['product_cost','opening_balance','seller_contract','operating_cost']
    name: str = Field(min_length=1,max_length=180)
    brand: str = Field(default='',max_length=120)
    sku: str = Field(default='',max_length=100)
    effectiveDate: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    amount: Decimal = Field(default=0,max_digits=16,decimal_places=2)
    commissionRate: Decimal | None = Field(default=None,ge=0,le=100,decimal_places=2)
    bonusRate: Decimal | None = Field(default=None,ge=0,le=100,decimal_places=2)
    basis: Literal['Faturado','Recebido'] = 'Faturado'
    contractReference: str = Field(default='',max_length=500)
    @model_validator(mode='after')
    def valid_input(self):
        from datetime import date
        date.fromisoformat(self.effectiveDate)
        if self.kind!='opening_balance' and self.amount<0:raise ValueError('Custo não pode ser negativo')
        if self.kind=='product_cost' and (not self.brand or not self.sku):raise ValueError('Marca e SKU obrigatórios')
        if self.kind=='seller_contract' and (self.commissionRate is None or not self.contractReference):raise ValueError('Percentual e referência contratual obrigatórios')
        return self
